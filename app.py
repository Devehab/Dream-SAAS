from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import sys
from functools import wraps
import google.generativeai as genai
import stripe
from dateutil.parser import parse

# Load environment variables
load_dotenv()

# Configure Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

# Configure Gemini AI
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-pro')

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# Constants for subscription plans
FREE_PLAN_DREAM_LIMIT = 7
FREE_PLAN_DAYS = 10
PREMIUM_PLAN_PRICE = 15.00

try:
    # Initialize Firebase
    if not os.path.exists('firebase-key.json'):
        raise FileNotFoundError("firebase-key.json file not found.")
    cred = credentials.Certificate('firebase-key.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase initialized successfully!")
except Exception as e:
    print(f"Error initializing Firebase: {str(e)}", file=sys.stderr)
    sys.exit(1)

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('الرجاء تسجيل الدخول أولاً.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def check_subscription_status(user_id):
    user_ref = db.collection('users').document(user_id)
    user_data = user_ref.get().to_dict()
    
    if not user_data:
        return False, "لم يتم العثور على معلومات المستخدم"
    
    # التحقق من الاشتراك المدفوع
    if user_data.get('plan_type') == 'premium':
        if user_data.get('subscription_end') and parse(user_data['subscription_end']) > datetime.now():
            return True, "اشتراك مدفوع نشط"
        return False, "انتهى الاشتراك المدفوع"
    
    # التحقق من الخطة المجانية
    if user_data.get('plan_type') == 'free':
        trial_end = parse(user_data['trial_end'])
        dreams_count = user_data.get('dreams_count', 0)
        
        # التحقق من انتهاء الفترة التجريبية
        if datetime.now() > trial_end:
            return False, "انتهت الفترة التجريبية المجانية"
        
        # التحقق من عدد الأحلام المتبقية
        if dreams_count >= FREE_PLAN_DREAM_LIMIT:
            return False, "تم استنفاد عدد الأحلام المجانية"
        
        days_left = (trial_end - datetime.now()).days
        dreams_left = FREE_PLAN_DREAM_LIMIT - dreams_count
        
        return True, f"متبقي {dreams_left} أحلام و {days_left} أيام في الفترة التجريبية"
    
    return False, "لم يتم العثور على خطة اشتراك"

def analyze_dream_type(description):
    try:
        prompt = f"""تحليل نوع الحلم التالي وتصنيفه:

        الحلم: {description}

        قم بتحديد نوع الحلم من خلال تحليل محتواه. يجب أن يكون التصنيف أحد الأنواع التالية:
        - رؤيا صالحة: إذا كان الحلم يحمل بشرى أو معانٍ إيجابية
        - حلم عادي: إذا كان من أحاديث النفس أو الخواطر اليومية
        - كابوس: إذا كان يحمل مشاعر سلبية أو مخاوف
        - حلم شيطاني: إذا كان يحتوي على محتوى غير لائق أو وساوس
        - حلم نبوي: إذا كانت علاماته تتوافق مع علامات الرؤيا الصادقة

        أعطني نوع الحلم فقط دون أي تفسير إضافي."""

        response = model.generate_content(prompt)
        dream_type = response.text.strip()
        return dream_type
    except Exception as e:
        print(f"Error analyzing dream type: {str(e)}")
        return "حلم عادي"

def clean_text(text):
    """تنظيف النص من علامات النجمة والعلامات الخاصة"""
    return text.replace('*', '').replace('**', '').strip()

def interpret_dream(dream_description, interpretation_source):
    try:
        # تحليل نوع الحلم
        dream_type = analyze_dream_type(dream_description)
        
        source_prompts = {
            'quran': 'استخدم القرآن الكريم كمصدر رئيسي للتفسير، مع ذكر الآيات القرآنية ذات الصلة.',
            'sunnah': 'استخدم السنة النبوية كمصدر رئيسي للتفسير، مع ذكر الأحاديث النبوية ذات الصلة.',
            'ibn_sireen': 'استخدم تفسيرات ابن سيرين كمصدر رئيسي للتفسير، مع ذكر المراجع من كتبه.'
        }
        
        source_text = source_prompts.get(interpretation_source, '')
        
        prompt = f"""أنت مفسر أحلام محترف. المطلوب تفسير الحلم التالي:

الحلم: {dream_description}

المصدر: {source_text}

قم بتقديم التفسير بالتنسيق التالي فقط، بدون أي علامات تنسيق أو ترميز:

التفسير التفصيلي:
[هنا يجب كتابة تفسير مفصل للحلم]

الرموز والدلالات:
- [الرمز الأول]: [معناه]
- [الرمز الثاني]: [معناه]

المراجع والمصادر:
- [المرجع الأول]
- [المرجع الثاني]

ملاحظات مهمة:
1. لا تستخدم علامات النجمة (*) أو أي علامات ترميز نهائياً
2. اكتب النص مباشرة بدون أي تنسيق خاص
3. لا تضع أي علامات حول العناوين أو النصوص
4. اترك سطر فارغ بين كل قسم"""

        print(f"Sending prompt to model: {prompt}")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt)
                interpretation_text = clean_text(response.text)
                print(f"Received response (attempt {attempt + 1}): {interpretation_text}")
                
                if not interpretation_text:
                    if attempt < max_retries - 1:
                        print(f"Empty response, retrying... (attempt {attempt + 1})")
                        continue
                    else:
                        raise Exception("Received empty response after all retries")
                
                sections = {
                    'detailed_interpretation': '',
                    'symbols': [],
                    'references': []
                }
                
                current_section = None
                for line in interpretation_text.split('\n'):
                    line = clean_text(line)
                    
                    if not line:
                        continue
                        
                    if 'التفسير التفصيلي:' in line:
                        current_section = 'detailed_interpretation'
                        continue
                    elif 'الرموز والدلالات:' in line:
                        current_section = 'symbols'
                        continue
                    elif 'المراجع والمصادر:' in line:
                        current_section = 'references'
                        continue
                    
                    if current_section == 'detailed_interpretation':
                        if sections['detailed_interpretation']:
                            sections['detailed_interpretation'] += '\n'
                        sections['detailed_interpretation'] += clean_text(line)
                    elif current_section == 'symbols' and line.startswith('-'):
                        parts = line[1:].split(':', 1)
                        if len(parts) == 2:
                            name = clean_text(parts[0])
                            meaning = clean_text(parts[1])
                            sections['symbols'].append({
                                'name': name,
                                'meaning': meaning
                            })
                    elif current_section == 'references' and line.startswith('-'):
                        ref = clean_text(line[1:])
                        if ref:
                            sections['references'].append(ref)
                
                print(f"Processed sections: {sections}")
                
                if not sections['detailed_interpretation']:
                    raise Exception("Missing detailed interpretation")
                
                return {
                    'dream_type': clean_text(dream_type),
                    'detailed_interpretation': sections['detailed_interpretation'],
                    'symbols': sections['symbols'],
                    'references': sections['references']
                }
                
            except Exception as e:
                print(f"Error in attempt {attempt + 1}: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                
    except Exception as e:
        print(f"Final error in interpret_dream: {str(e)}")
        return {
            'dream_type': 'حلم عادي',
            'detailed_interpretation': 'عذراً، حدث خطأ أثناء تفسير الحلم. الرجاء المحاولة مرة أخرى.',
            'symbols': [],
            'references': []
        }

@app.route('/')
def landing():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/dashboard')
@login_required
def dashboard():
    try:
        # Get subscription status
        can_interpret, status_message = check_subscription_status(session['user_id'])
        
        # Get user info
        user_ref = db.collection('users').document(session['user_id'])
        user_data = user_ref.get().to_dict()
        
        # Get user's dreams
        dreams_ref = db.collection('dreams')
        dreams_ref = dreams_ref.where('user_id', '==', session['user_id'])
        docs = dreams_ref.stream()
        dreams = []
        for doc in docs:
            dream_data = doc.to_dict()
            dream_data['id'] = doc.id
            dreams.append(dream_data)
        dreams.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return render_template('dashboard.html', 
                             dreams=dreams, 
                             can_interpret=can_interpret, 
                             status_message=status_message,
                             stripe_key=os.getenv('STRIPE_PUBLISHABLE_KEY'),
                             user=user_data)
    except Exception as e:
        flash('حدث خطأ في تحميل البيانات', 'danger')
        print(f"Error loading dashboard: {str(e)}")
        return render_template('dashboard.html', dreams=[], can_interpret=False, status_message="حدث خطأ")

@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': os.getenv('STRIPE_PRICE_ID'),
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('subscription_success', _external=True),
            cancel_url=url_for('subscription_cancel', _external=True),
            client_reference_id=session['user_id']
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return jsonify(error=str(e)), 403

@app.route('/subscription/success')
@login_required
def subscription_success():
    user_ref = db.collection('users').document(session['user_id'])
    user_ref.update({
        'plan_type': 'premium',
        'subscription_start': datetime.now().isoformat(),
        'subscription_end': (datetime.now() + timedelta(days=30)).isoformat()
    })
    flash('تم تفعيل الاشتراك المدفوع بنجاح!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/subscription/cancel')
@login_required
def subscription_cancel():
    flash('تم إلغاء عملية الدفع', 'warning')
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            user = auth.get_user_by_email(email)
            session['user_id'] = user.uid
            session['email'] = user.email
            flash('تم تسجيل الدخول بنجاح!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            flash('خطأ في تسجيل الدخول. تأكد من البريد الإلكتروني وكلمة المرور.', 'danger')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            # Create user in Firebase Authentication
            user = auth.create_user(
                email=email,
                password=password,
                display_name=name
            )
            
            # Create user document in Firestore
            user_ref = db.collection('users').document(user.uid)
            user_ref.set({
                'name': name,
                'email': email,
                'created_at': datetime.now(),
                'plan_type': 'free',
                'dreams_count': 0,
                'trial_end': (datetime.now() + timedelta(days=FREE_PLAN_DAYS)).isoformat()
            })
            
            session['user_id'] = user.uid
            session['email'] = email
            flash('تم إنشاء الحساب بنجاح!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            flash('حدث خطأ في إنشاء الحساب. الرجاء المحاولة مرة أخرى.', 'danger')
            print(f"Error in registration: {str(e)}")
    
    return render_template('register.html')

@app.route('/add_dream', methods=['POST'])
def add_dream():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        # التحقق من حالة الاشتراك
        can_interpret, message = check_subscription_status(session['user_id'])
        if not can_interpret:
            flash(message, 'warning')
            return redirect(url_for('dashboard'))
            
        # تفسير الحلم
        dream_description = request.form.get('description')
        interpretation_source = request.form.get('interpretation_source')
        
        # طباعة البيانات المستلمة للتحقق
        print(f"Received dream: {dream_description}")
        print(f"Source: {interpretation_source}")
        
        interpretation = interpret_dream(dream_description, interpretation_source)
        
        # طباعة نتيجة التفسير للتحقق
        print(f"Interpretation result: {interpretation}")
        
        # إضافة الحلم إلى قاعدة البيانات
        dream_data = {
            'user_id': session['user_id'],
            'description': dream_description,
            'dream_type': interpretation.get('dream_type', ''),
            'interpretation_source': interpretation_source,
            'detailed_interpretation': interpretation.get('detailed_interpretation', ''),
            'symbols': interpretation.get('symbols', []),
            'references': interpretation.get('references', []),
            'timestamp': datetime.now()
        }
        
        # طباعة البيانات قبل الحفظ
        print(f"Data to be saved: {dream_data}")
        
        # حفظ الحلم
        dream_ref = db.collection('dreams').document()
        dream_ref.set(dream_data)
        
        # تحديث عداد الأحلام للمستخدم
        user_ref = db.collection('users').document(session['user_id'])
        user_ref.update({
            'dreams_count': firestore.Increment(1)
        })
        
        flash('تم حفظ الحلم وتفسيره بنجاح', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"Error in add_dream: {str(e)}")
        flash('حدث خطأ أثناء حفظ الحلم. الرجاء المحاولة مرة أخرى.', 'error')
        return redirect(url_for('dashboard'))

@app.route('/delete_dream/<dream_id>', methods=['POST'])
@login_required
def delete_dream(dream_id):
    dream_ref = db.collection('dreams').document(dream_id)
    dream = dream_ref.get()
    
    if dream.exists and dream.to_dict()['user_id'] == session['user_id']:
        dream_ref.delete()
        flash('تم حذف الحلم بنجاح!', 'success')
    else:
        flash('لا يمكنك حذف هذا الحلم!', 'danger')
    
    return redirect(url_for('dashboard'))

@app.route('/logout')
@login_required
def logout():
    session.clear()
    flash('تم تسجيل الخروج بنجاح', 'success')
    return redirect(url_for('landing'))

@app.route('/webhooks', methods=['POST'])
def stripe_webhook():
    # تحقق من وجود التوقيع في الطلب
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as e:
        # في حالة عدم صحة payload
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError as e:
        # في حالة عدم صحة التوقيع
        return jsonify({'error': 'Invalid signature'}), 400

    # معالجة الأحداث المختلفة
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        
        # استخراج معرف المستخدم من الجلسة
        user_id = session.get('client_reference_id')
        if not user_id:
            return jsonify({'error': 'No client_reference_id found'}), 400

        # الحصول على تفاصيل الاشتراك
        subscription = stripe.Subscription.retrieve(session.subscription)
        
        # حساب تاريخ انتهاء الاشتراك (30 يوم من الآن)
        subscription_end = datetime.fromtimestamp(subscription.current_period_end)
        
        # تحديث معلومات المستخدم في Firestore
        user_ref = db.collection('users').document(user_id)
        user_ref.update({
            'plan_type': 'premium',
            'subscription_id': subscription.id,
            'subscription_status': subscription.status,
            'subscription_start': datetime.fromtimestamp(subscription.current_period_start).isoformat(),
            'subscription_end': subscription_end.isoformat(),
            'stripe_customer_id': session.customer,
            'last_payment_date': datetime.now().isoformat(),
            'payment_amount': session.amount_total / 100,  # تحويل من السنتات إلى الدولارات
            'payment_status': 'completed'
        })

    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        
        # البحث عن المستخدم باستخدام stripe_customer_id
        users_ref = db.collection('users')
        query = users_ref.where('stripe_customer_id', '==', subscription.customer).limit(1)
        user_docs = query.get()
        
        if not user_docs:
            return jsonify({'error': 'User not found'}), 404
            
        user_doc = user_docs[0]
        user_ref = db.collection('users').document(user_doc.id)
        
        user_ref.update({
            'subscription_status': subscription.status,
            'subscription_end': datetime.fromtimestamp(subscription.current_period_end).isoformat(),
            'last_updated': datetime.now().isoformat()
        })

    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        
        # البحث عن المستخدم باستخدام stripe_customer_id
        users_ref = db.collection('users')
        query = users_ref.where('stripe_customer_id', '==', subscription.customer).limit(1)
        user_docs = query.get()
        
        if not user_docs:
            return jsonify({'error': 'User not found'}), 404
            
        user_doc = user_docs[0]
        user_ref = db.collection('users').document(user_doc.id)
        
        # تحديث حالة المستخدم إلى free عند إلغاء الاشتراك
        user_ref.update({
            'plan_type': 'free',
            'subscription_status': 'canceled',
            'subscription_end': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat()
        })

    return jsonify({'status': 'success'}), 200

@app.route('/subscription/manage')
@login_required
def manage_subscription():
    try:
        user_ref = db.collection('users').document(session['user_id'])
        user_data = user_ref.get().to_dict()
        
        subscription_data = None
        if user_data.get('subscription_id'):
            subscription = stripe.Subscription.retrieve(user_data['subscription_id'])
            subscription_data = {
                'status': subscription.status,
                'current_period_end': datetime.fromtimestamp(subscription.current_period_end),
                'cancel_at_period_end': subscription.cancel_at_period_end
            }
        
        return render_template('subscription.html',
                             user=user_data,
                             subscription=subscription_data,
                             stripe_key=os.getenv('STRIPE_PUBLISHABLE_KEY'))
    except Exception as e:
        flash('حدث خطأ في تحميل معلومات الاشتراك', 'danger')
        print(f"Error loading subscription: {str(e)}")
        return redirect(url_for('dashboard'))

@app.route('/subscription/cancel', methods=['POST'])
@login_required
def cancel_subscription():
    try:
        user_ref = db.collection('users').document(session['user_id'])
        user_data = user_ref.get().to_dict()
        
        if not user_data.get('subscription_id'):
            flash('لا يوجد اشتراك نشط للإلغاء', 'warning')
            return redirect(url_for('manage_subscription'))
        
        # إلغاء الاشتراك في نهاية الفترة الحالية
        subscription = stripe.Subscription.modify(
            user_data['subscription_id'],
            cancel_at_period_end=True
        )
        
        flash('سيتم إلغاء اشتراكك في نهاية فترة الفوترة الحالية', 'info')
        return redirect(url_for('manage_subscription'))
    except Exception as e:
        flash('حدث خطأ في إلغاء الاشتراك', 'danger')
        print(f"Error canceling subscription: {str(e)}")
        return redirect(url_for('manage_subscription'))

@app.route('/subscription/resume', methods=['POST'])
@login_required
def resume_subscription():
    try:
        user_ref = db.collection('users').document(session['user_id'])
        user_data = user_ref.get().to_dict()
        
        if not user_data.get('subscription_id'):
            flash('لا يوجد اشتراك للاستئناف', 'warning')
            return redirect(url_for('manage_subscription'))
        
        # إلغاء الإلغاء المجدول
        subscription = stripe.Subscription.modify(
            user_data['subscription_id'],
            cancel_at_period_end=False
        )
        
        flash('تم استئناف اشتراكك بنجاح', 'success')
        return redirect(url_for('manage_subscription'))
    except Exception as e:
        flash('حدث خطأ في استئناف الاشتراك', 'danger')
        print(f"Error resuming subscription: {str(e)}")
        return redirect(url_for('manage_subscription'))

if __name__ == '__main__':
    app.run(debug=True, port=8080)
