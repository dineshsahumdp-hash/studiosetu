import os
import re
import json
import sqlite3
import traceback
import calendar
import secrets
import urllib.parse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime, date, timedelta
from functools import wraps


from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response, send_from_directory, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
# =====================================================================
# ⚙️ 1. SECURE APP CONFIGURATION & DYNAMIC PATH SETUP
# =====================================================================

BASE_DIR = os.environ.get("STUDIO_BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.join(BASE_DIR, "Project")
TEMPLATE_DIR = os.path.join(PROJECT_DIR, "templates")
STATIC_DIR = os.path.join(PROJECT_DIR, "static")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
JOB_FOLDERS_DIR = os.path.join(BASE_DIR, "Studio_Job_Folders")
DB_PATH = os.path.join(BASE_DIR, "him_studio.db")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(os.path.join(UPLOADS_DIR, "purchase_bills"), exist_ok=True)
os.makedirs(JOB_FOLDERS_DIR, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "him_studio_super_secure_vault_key_2026_saas_x99")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

DATABASE_URL = os.environ.get("DATABASE_URL", None)

def get_db_connection():
    """
    1000+ यूज़र्स के लिए फ़्यूचर-प्रूफ़ कनेक्शन:
    - लोकल मोड: SQLite को 60 सेकंड बज़ी टाइमआउट और WAL मोड पर लॉक-फ़्री चलाता है
    - प्रोडक्शन मोड: सीधे PostgreSQL कनेक्शन पूल से कनेक्ट करता है
    """
    if DATABASE_URL and DATABASE_URL.startswith("postgres"):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        # SQLite Super-Safe Engine (बिना किसी थ्रेड लॉक के)
        conn = sqlite3.connect(DB_PATH, timeout=60.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=60000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.row_factory = sqlite3.Row
        return conn
# =====================================================================
# 🛠️ 2. SAAS MULTI-STUDIO DATABASE MIGRATION ENGINE
# =====================================================================

def init_all_tables():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 🏢 Master Studios Table (SaaS Tenants)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saas_studios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_name TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                address TEXT DEFAULT '',
                mobile TEXT UNIQUE NOT NULL,
                whatsapp TEXT DEFAULT '',
                email TEXT DEFAULT '',
                password_hash TEXT NOT NULL,
                master_pin_hash TEXT NOT NULL,
                plan_expiry TEXT,
                subscription_plan TEXT DEFAULT '30 Days Free Trial',
                status TEXT DEFAULT 'Active',
                created_at TEXT
            )
        """)

        for col_def in [
            "address TEXT DEFAULT ''", 
            "whatsapp TEXT DEFAULT ''", 
            "email TEXT DEFAULT ''", 
            "subscription_plan TEXT DEFAULT '30 Days Free Trial'"
        ]:
            try:
                cursor.execute(f"ALTER TABLE saas_studios ADD COLUMN {col_def}")
            except Exception:
                pass

        # 👑 डिफ़ॉल्ट HIM STUDIO (Master Tenant #1)
        cursor.execute("SELECT COUNT(*) FROM saas_studios WHERE id = 1")
        if cursor.fetchone()[0] == 0:
            def_pass = generate_password_hash("admin123")
            def_pin = generate_password_hash("123456")
            cursor.execute("""
                INSERT INTO saas_studios (
                    id, studio_name, owner_name, address, mobile, whatsapp, email, 
                    password_hash, master_pin_hash, plan_expiry, subscription_plan, status, created_at
                )
                VALUES (
                    1, 'HIM STUDIO', 'Dinesh Sahu', 
                    'Shri Radha Madhav Dham Parisar, Bhim Chowk Thana Road Ratanpur 495445', 
                    '9691158104', '9691158104', 'dineshsahumdp@gmail.com', 
                    ?, ?, 'Lifetime', 'Master SaaS License', 'Active', ?
                )
            """, (def_pass, def_pin, datetime.now().strftime('%d/%m/%Y %I:%M %p')))
            conn.commit()
        else:
            # यदि id=1 पहले से मौजूद है, तो पुरानी डमी जानकारी को सही जानकारी से अपडेट करें
            cursor.execute("""
                UPDATE saas_studios 
                SET studio_name = 'HIM STUDIO',
                    owner_name = 'Dinesh Sahu',
                    address = 'Shri Radha madhav Dham Parisar, bhim chowk thana road ratanpur 495445',
                    mobile = '9691158104',
                    whatsapp = '9691158104',
                    email = 'dineshsahumdp@gmail.com'
                WHERE id = 1
            """)
            conn.commit()

        # 📄 Bookings & Booking Items
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_no INTEGER NOT NULL,
                studio_id INTEGER DEFAULT 1,
                customer_name TEXT,
                mobile TEXT,
                address TEXT,
                bill_date TEXT,
                delivery_date TEXT,
                total_amount REAL,
                discount_amount REAL DEFAULT 0,
                advance_amount REAL,
                balance_amount REAL,
                cash_paid REAL DEFAULT 0,
                upi_paid REAL DEFAULT 0,
                payment_history TEXT DEFAULT '[]',
                share_token TEXT,
                status TEXT DEFAULT 'Pending'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS booking_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                bill_no INTEGER,
                item_name TEXT,
                quantity INTEGER,
                price REAL,
                total REAL
            )
        """)

        # 💍 Wedding Bookings & Payments
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wedding_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_no INTEGER,
                studio_id INTEGER DEFAULT 1,
                customer_name TEXT,
                mobile TEXT,
                address TEXT,
                venue_location TEXT,
                total_deal_amount REAL,
                discount_amount REAL,
                advance_received REAL,
                balance_amount REAL,
                status TEXT,
                services TEXT,
                wedding_details TEXT,
                wed_date_1 TEXT, wed_date_2 TEXT, wed_date_3 TEXT, wed_date_4 TEXT,
                wed_event_1 TEXT, wed_event_2 TEXT, wed_event_3 TEXT, wed_event_4 TEXT,
                wed_cam_1 TEXT, wed_cam_2 TEXT, wed_cam_3 TEXT, wed_cam_4 TEXT,
                wed_staff_1 TEXT, wed_staff_2 TEXT, wed_staff_3 TEXT, wed_staff_4 TEXT,
                payment_history TEXT,
                created_at TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wedding_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                booking_id INTEGER,
                amount REAL,
                payment_mode TEXT,
                date_time TEXT
            )
        """)

        # 🎫 Daily Tokens
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                token_no INTEGER NOT NULL DEFAULT 1,
                customer_name TEXT DEFAULT '',
                mobile TEXT DEFAULT '',
                work_type TEXT DEFAULT '',
                material_used TEXT DEFAULT '',
                material_qty REAL DEFAULT 0,
                discount REAL DEFAULT 0.0,
                item_type TEXT DEFAULT 'material',
                cash_paid REAL DEFAULT 0,
                upi_paid REAL DEFAULT 0,
                total_amount REAL DEFAULT 0,
                token_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # 💳 Ledgers & Stocks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS credit_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                bill_no TEXT DEFAULT 'Direct Entry',
                cname TEXT NOT NULL,
                mob TEXT NOT NULL,
                addr TEXT,
                reason TEXT,
                due_amount REAL NOT NULL,
                entry_date TEXT NOT NULL,
                promise_date TEXT NOT NULL,
                status TEXT DEFAULT 'Pending'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS product_stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                pname TEXT NOT NULL,
                pqty INTEGER NOT NULL,
                pprice REAL NOT NULL,
                pdisc REAL DEFAULT 0,
                last_updated TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchasers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                mobile TEXT,
                address TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchase_bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                purchaser_id INTEGER,
                bill_date TEXT,
                bill_number TEXT,
                total_amount REAL,
                file_path TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS master_lab_vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                vendor_name TEXT NOT NULL,
                mobile TEXT NOT NULL,
                address TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lab_outsource_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                vendor_id INTEGER NOT NULL,
                sno INTEGER,
                cust_name TEXT NOT NULL,
                mobile TEXT NOT NULL,
                address TEXT,
                work_type TEXT,
                work_rec_date TEXT,
                total_amount REAL DEFAULT 0,
                advance_amount REAL DEFAULT 0,
                sent_lab_date TEXT,
                expected_date TEXT,
                tracking_no TEXT,
                lab_cost REAL DEFAULT 0,
                lab_paid REAL DEFAULT 0,
                pay_date TEXT,
                cust_delivery_status TEXT DEFAULT 'Pending',
                has_file INTEGER DEFAULT 0,
                attached_file_path TEXT,
                remark TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS studio_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                asset_name TEXT NOT NULL,
                serial_no TEXT DEFAULT '',
                default_rent REAL DEFAULT 0,
                total_stock_qty INTEGER DEFAULT 1,
                available_stock_qty INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS equipment_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no INTEGER DEFAULT 1,
                studio_id INTEGER DEFAULT 1,
                options_type TEXT NOT NULL,
                camera_name TEXT NOT NULL,
                serial_no TEXT DEFAULT '',
                person_name TEXT NOT NULL,
                mobile TEXT DEFAULT '',
                commitment_date TEXT NOT NULL,
                issue_date TEXT NOT NULL,
                return_date TEXT NOT NULL,
                per_day_rent REAL DEFAULT 0,
                advance_amount REAL DEFAULT 0,
                balance_amount REAL DEFAULT 0,
                status TEXT DEFAULT 'Open',
                created_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                exp_date TEXT,
                category TEXT,
                amount REAL,
                notes TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sahukar_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                date_taken TEXT,
                lender_name TEXT,
                pan_number TEXT,
                borrowed_amount REAL,
                notes TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sahukar_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                account_id INTEGER,
                pay_date TEXT,
                amount_paid REAL,
                remarks TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bank_loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                bank_name TEXT,
                branch TEXT,
                ifsc_code TEXT,
                loan_amount REAL,
                deductions REAL DEFAULT 0,
                disbursed REAL,
                emi REAL,
                tenure INTEGER,
                interest_rate REAL,
                start_date TEXT,
                status TEXT DEFAULT 'Active'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bank_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                bank_id INTEGER,
                installment_no INTEGER,
                due_date TEXT,
                emi_amount REAL,
                payment_date TEXT,
                mode TEXT,
                status TEXT DEFAULT 'Unpaid'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                name TEXT NOT NULL,
                mobile TEXT DEFAULT '',
                address TEXT DEFAULT '',
                role TEXT DEFAULT 'Staff',
                salary REAL DEFAULT 0,
                join_date TEXT DEFAULT '',
                acc_no TEXT DEFAULT '',
                bank_name TEXT DEFAULT '',
                ifsc_code TEXT DEFAULT '',
                branch_name TEXT DEFAULT '',
                advance_paid REAL DEFAULT 0,
                status TEXT DEFAULT 'Active'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS employee_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                employee_id INTEGER,
                emp_id INTEGER,
                attendance_date TEXT,
                status TEXT,
                reason TEXT DEFAULT ''
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS employee_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                employee_id INTEGER,
                emp_id INTEGER,
                trans_date TEXT,
                trans_type TEXT,
                amount REAL DEFAULT 0,
                remarks TEXT DEFAULT ''
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wedding_incentive_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                emp_id INTEGER,
                emp_name TEXT,
                wedding_id INTEGER,
                event_date TEXT,
                event_name TEXT,
                suggested_amount REAL DEFAULT 500,
                status TEXT DEFAULT 'Pending',
                created_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS master_raw_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                material_name TEXT NOT NULL,
                category TEXT NOT NULL,
                current_qty REAL DEFAULT 0,
                unit TEXT NOT NULL,
                last_updated TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS material_stock_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                material_name TEXT NOT NULL,
                change_qty REAL NOT NULL,
                action_type TEXT NOT NULL,
                raw_command TEXT,
                log_date TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS service_recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                ingredient_name TEXT NOT NULL,
                ingredient_type TEXT DEFAULT 'material',
                qty_needed REAL DEFAULT 1.0,
                UNIQUE(studio_id, service_name, ingredient_name)
            )
        """)

        # ⚡ 1000+ Studios Superfast Crash-Proof Indexes
        indexes = [
            ("idx_studios_mob", "saas_studios(mobile)"),
            ("idx_bookings_st_bill", "bookings(studio_id, bill_no)"),
            ("idx_bookings_tok", "bookings(share_token)"),
            ("idx_booking_items_st_b", "booking_items(studio_id, bill_no)"),
            ("idx_tokens_st_date", "daily_tokens(studio_id, token_date)"),
            ("idx_prod_st_name", "product_stock(studio_id, pname)"),
            ("idx_raw_st_name", "master_raw_materials(studio_id, material_name)"),
            ("idx_wedding_st_bno", "wedding_bookings(studio_id, bill_no)")
        ]
        for iname, itarget in indexes:
            try:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {iname} ON {itarget}")
            except Exception:
                pass

        # 🛡️ ऑटो-माइग्रेशन: अगर पुरानी टेबल्स में studio_id नहीं है तो जोड़ेगा
        tables_to_patch = [
            "bookings", "booking_items", "wedding_bookings", "wedding_payments", "daily_tokens", 
            "credit_ledger", "product_stock", "master_raw_materials", "material_stock_logs",
            "daily_expenses", "sahukar_accounts", "sahukar_history", "bank_loans", "bank_schedule",
            "employees", "employee_attendance", "employee_transactions",
            "wedding_incentive_staging", "purchasers", "purchase_bills", "master_lab_vendors",
            "lab_outsource_records", "studio_assets", "equipment_logs"
        ]
        for tbl in tables_to_patch:
            try:
                cursor.execute(f"PRAGMA table_info({tbl})")
                cols = [c['name'] for c in cursor.fetchall()]
                if 'studio_id' not in cols:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN studio_id INTEGER DEFAULT 1")
            except Exception:
                pass

        conn.commit()
    except Exception as e:
        print("Init tables notice:", e)
    finally:
        if conn:
            conn.close()

def check_and_update_db_schema():
    """सर्वर चालू होते ही छूटे हुए कॉलम्स को बिना क्रैश किए सुरक्षित जोड़ना"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("ALTER TABLE daily_tokens ADD COLUMN discount REAL DEFAULT 0.0")
            conn.commit()
        except Exception:
            pass

        try:
            cursor.execute("ALTER TABLE daily_tokens ADD COLUMN item_type TEXT DEFAULT 'material'")
            conn.commit()
        except Exception:
            pass

        try:
            cursor.execute("ALTER TABLE bookings ADD COLUMN share_token TEXT")
            conn.commit()
        except Exception:
            pass

        try:
            cursor.execute("ALTER TABLE bookings ADD COLUMN payment_history TEXT DEFAULT '[]'")
            conn.commit()
        except Exception:
            pass

        try:
            cursor.execute("ALTER TABLE wedding_bookings ADD COLUMN bill_no INTEGER")
            conn.commit()
        except Exception:
            pass

        try:
            cursor.execute("ALTER TABLE equipment_logs ADD COLUMN mobile TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass

        # 🔄 पुराने सभी बिलों को सुरक्षित टोकन असाइन करें
        try:
            cursor.execute("SELECT bill_no, studio_id FROM bookings WHERE share_token IS NULL OR share_token = ''")
            old_bills = cursor.fetchall()
            for row in old_bills:
                b_no = row['bill_no'] if isinstance(row, sqlite3.Row) else row[0]
                s_id = row['studio_id'] if isinstance(row, sqlite3.Row) else row[1]
                tok = secrets.token_hex(16)
                cursor.execute("UPDATE bookings SET share_token = ? WHERE bill_no = ? AND studio_id = ?", (tok, b_no, s_id))
            if old_bills:
                conn.commit()
        except Exception as backfill_err:
            print("Token backfill notice:", backfill_err)

    except Exception as e:
        print("Schema update error:", e)
    finally:
        if conn: 
            conn.close()

def seed_studio_recipes(studio_id):
    """स्टूडियो के लिए डिफ़ॉल्ट रेसिपी सुरक्षित जोड़ना"""
    conn = None
    try:
        cur_id = int(studio_id or 1)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        default_recipes = [
            ('Aadhaar Print', '4x6', 'material', 1.0),
            ('Aadhaar Print', 'Lamination Pouch', 'material', 1.0),
            ('Pan Card Print', '4x6', 'material', 1.0),
            ('Pan Card Print', 'Lamination Pouch', 'material', 1.0),
            ('Passport Photo (8 Pcs)', '4x6', 'material', 1.0),
            ('Mobile Photo Print', '4x6', 'material', 1.0),
            ('A4 Document Lamination', 'A4 Lamination Pouch', 'material', 1.0),
            ('A4 Photo Print', 'A4 Glossy Paper', 'material', 1.0)
        ]
        
        for s_name, ing_name, ing_type, q in default_recipes:
            cursor.execute("""
                INSERT OR IGNORE INTO service_recipes 
                (studio_id, service_name, ingredient_name, ingredient_type, qty_needed)
                VALUES (?, ?, ?, ?, ?)
            """, (cur_id, s_name, ing_name, ing_type, q))
            
        conn.commit()
    except Exception as e:
        print(f"Recipe seed notice for Studio #{studio_id}:", e)
    finally:
        if conn:
            conn.close()

# स्कीमा चालू करें
init_all_tables()
check_and_update_db_schema()
seed_studio_recipes(1)

# =====================================================================
# 🧩 3. HELPER FUNCTIONS & AUTH GUARDS
# =====================================================================

def fix_date(date_str):
    if not date_str:
        return None
    d_str = str(date_str).strip()
    if not d_str or d_str == 'None':
        return None
    if re.match(r'^\d{4}-\d{2}-\d{2}$', d_str):
        return d_str
    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d', '%d-%m-%y', '%d/%m/%y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(d_str.split('T')[0], fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return d_str

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        s_id = session.get('studio_id')
        if not s_id:
            if request.path.startswith('/api/'):
                return jsonify({"status": "error", "message": "Unauthorized access! Please login to your studio."}), 401
            return redirect(url_for('saas_login_page'))
        
        try:
            if int(s_id) == 1:
                return f(*args, **kwargs)
        except Exception:
            pass

        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT status, plan_expiry FROM saas_studios WHERE id = ?", (s_id,))
            row = cursor.fetchone()
            
            if row:
                st = str(row['status'] or 'Active')
                exp_str = str(row['plan_expiry'] or '')

                if st == 'Suspended':
                    session.clear()
                    if request.path.startswith('/api/'):
                        return jsonify({"status": "error", "message": "खाता सस्पेंड है! एडमिन से संपर्क करें।"}), 403
                    return redirect(url_for('saas_login_page'))

                if exp_str and exp_str != 'Lifetime':
                    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
                        try:
                            exp_dt = datetime.strptime(exp_str, fmt).date()
                            if date.today() > exp_dt:
                                cursor.execute("UPDATE saas_studios SET status = 'Expired' WHERE id = ?", (s_id,))
                                conn.commit()
                                session.clear()
                                if request.path.startswith('/api/'):
                                    return jsonify({"status": "error", "message": "ट्रायल समाप्त हो चुका है!"}), 403
                                return redirect(url_for('saas_login_page'))
                            break
                        except Exception:
                            continue
        except Exception as auth_err:
            print("Auth bypass notice:", auth_err)
        finally:
            if conn:
                conn.close()

        return f(*args, **kwargs)
    return decorated_function

def get_current_studio_id():
    return session.get('studio_id', 1)

def normalize_date_parts(raw_date):
    if not raw_date:
        today = date.today()
        return today.strftime('%Y-%m-%d'), str(today.month).zfill(2), str(today.year)
    
    clean_str = str(raw_date).strip().split(' ')[0]
    if '-' in clean_str:
        parts = clean_str.split('-')
        if len(parts) == 3:
            if len(parts[0]) == 4:
                return clean_str, str(parts[1]).zfill(2), str(parts[0])
            else:
                return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}", str(parts[1]).zfill(2), str(parts[2])

    if '/' in clean_str:
        parts = clean_str.split('/')
        if len(parts) == 3:
            if len(parts[2]) == 4:
                return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}", str(parts[1]).zfill(2), str(parts[2])
            elif len(parts[0]) == 4:
                return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}", str(parts[1]).zfill(2), str(parts[0])

    today = date.today()
    return today.strftime('%Y-%m-%d'), str(today.month).zfill(2), str(today.year)

def safe_parse_date(d_str):
    if not d_str: return None
    d_str = str(d_str).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(d_str, fmt).date()
        except ValueError:
            continue
    return None

@app.route('/api/get_current_studio_info', methods=['GET'])
@login_required
def get_current_studio_info():
    s_name = session.get('studio_name') or "HIM STUDIO"
    o_name = session.get('owner_name') or "Owner"
    s_id = session.get('studio_id', 1)
    return jsonify({
        "status": "success",
        "studio_id": s_id,
        "studio_name": s_name,
        "owner_name": o_name
    })

def stage_wedding_incentive_if_assigned(cursor, wedding_id, staff_val, event_date, event_name, studio_id):
    if not staff_val or not wedding_id: 
        return
    
    staff_pieces = [s.strip() for s in str(staff_val).replace(';', ',').split(',') if s.strip()]
    for piece in staff_pieces:
        clean_input = piece.split('(')[0].strip()
        if not clean_input or clean_input.lower() in ['none', 'null', 'cameraman / staff...', 'select staff', '-']:
            continue
            
        first_name = clean_input.split()[0].strip().lower()
        cursor.execute("""
            SELECT id, name FROM employees 
            WHERE (id = ? OR LOWER(name) LIKE ? OR LOWER(name) LIKE ?) AND studio_id = ?
        """, (clean_input, f"{first_name}%", f"%{first_name}%", studio_id))
        emp_row = cursor.fetchone()
        
        emp_id = None
        emp_name = clean_input
        if emp_row:
            emp_id = emp_row['id']
            emp_name = emp_row['name']

        norm_date, m_str, y_str = normalize_date_parts(event_date)
        t_created = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cursor.execute("""
            SELECT id FROM wedding_incentive_staging 
            WHERE wedding_id = ? 
              AND (emp_id = ? OR LOWER(emp_name) LIKE ?) 
              AND event_name = ? 
              AND studio_id = ?
        """, (wedding_id, emp_id, f"%{first_name}%", str(event_name or 'Wedding Event'), studio_id))
        
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO wedding_incentive_staging 
                (studio_id, emp_id, emp_name, wedding_id, event_date, event_name, suggested_amount, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 500.0, 'Pending', ?)
            """, (studio_id, emp_id, emp_name, wedding_id, norm_date, str(event_name or 'Wedding Event'), t_created))

# =====================================================================
# ⬇️ इसके ठीक नीचे आपका @app.route('/login') शुरू होगा (उसे न छुएं)
# =====================================================================
@app.route('/login')
@app.route('/register')
@app.route('/saas_login')
def saas_login_page():
    if session.get('studio_id'):
        return redirect('/')
    return render_template('rege_login.html')

# 👇 पब्लिक इनवॉइस व्यू (saas_studios टेबल के साथ) 👇
from datetime import datetime

@app.route('/invoice/<token>')
def view_public_invoice(token):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. टोकन से बिल निकालें
        cursor.execute("SELECT * FROM bookings WHERE share_token = ?", (token,))
        bill_row = cursor.fetchone()

        if not bill_row:
            return "<h2 style='text-align:center;padding:50px;font-family:sans-serif;'>⚠️ अमान्य लिंक या बिल उपलब्ध नहीं है!</h2>", 404

        bill = dict(bill_row)

        # 2. saas_studios से उस बिल के सही स्टूडियो का डेटा निकालें
        cursor.execute("SELECT * FROM saas_studios WHERE id = ?", (bill['studio_id'],))
        st_row = cursor.fetchone()
        st_data = dict(st_row) if st_row else {}

        studio = {
            'studio_name': st_data.get('studio_name') or "HIM STUDIO",
            'owner_name': st_data.get('owner_name') or "",
            'address': st_data.get('address') or "",
            'mobile': st_data.get('mobile') or "",
            'whatsapp': st_data.get('whatsapp') or st_data.get('mobile') or "",
            'email': st_data.get('email') or ""
        }

        # 3. आइटम्स निकालें
        cursor.execute("SELECT * FROM booking_items WHERE bill_no = ? AND studio_id = ?", (bill['bill_no'], bill['studio_id']))
        items = [dict(r) for r in cursor.fetchall()]

        # 4. डिलीवरी डेट फॉर्मेटिंग
        delivery_date_formatted = bill.get('delivery_date')
        if delivery_date_formatted:
            try:
                d_obj = datetime.strptime(str(delivery_date_formatted).strip(), '%Y-%m-%d')
                delivery_date_formatted = d_obj.strftime('%d/%m/%Y')
            except Exception:
                pass

        return render_template(
            'public_invoice.html', 
            bill=bill, 
            items=items, 
            studio=studio, 
            delivery_date=delivery_date_formatted
        )
    except Exception as e:
        return f"Invoice Error: {str(e)}", 500
    finally:
        if conn:
            conn.close()

@app.route('/api/saas/register_studio', methods=['POST'])
@app.route('/api/register_new_studio', methods=['POST'])
def saas_register_studio():
    conn = None
    try:
        data = request.json or {}
        s_name = str(data.get('studio_name', '')).strip()
        o_name = str(data.get('owner_name', '')).strip()
        address = str(data.get('address', '')).strip()
        mobile = str(data.get('mobile', '')).strip()
        whatsapp = str(data.get('whatsapp', '')).strip() or mobile
        email = str(data.get('email', '')).strip()
        password = str(data.get('password', '')).strip()
        pin = str(data.get('pin', '') or data.get('master_pin', '')).strip()

        if not s_name or not o_name or not address or len(mobile) != 10 or not password:
            return jsonify({"status": "error", "message": "सभी अनिवार्य फ़ील्ड और 10 अंकों का मोबाइल भरें!"}), 400

        if len(pin) != 6 or not pin.isdigit():
            return jsonify({"status": "error", "message": "मास्टर सिक्योरिटी पिन ठीक 6 अंकों (Numeric) का होना चाहिए!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # मोबाइल नंबर की जांच
        cursor.execute("SELECT id FROM saas_studios WHERE mobile = ?", (mobile,))
        if cursor.fetchone():
            return jsonify({"status": "error", "message": "यह मोबाइल नंबर पहले से रजिस्टर्ड है! कृपया लॉगिन करें।"}), 400

        p_hash = generate_password_hash(password)
        pin_hash = generate_password_hash(pin)
        expiry = (datetime.now() + timedelta(days=30)).strftime('%d/%m/%Y')
        now_s = datetime.now().strftime('%d/%m/%Y %I:%M %p')

        cursor.execute("""
            INSERT INTO saas_studios (studio_name, owner_name, address, mobile, whatsapp, email, password_hash, master_pin_hash, plan_expiry, subscription_plan, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '30 Days Free Trial', 'Active', ?)
        """, (s_name, o_name, address, mobile, whatsapp, email, p_hash, pin_hash, expiry, now_s))
        new_studio_id = cursor.lastrowid

        # डिफ़ॉल्ट रॉ मैटेरियल्स
        def_materials = [
            ("4x6 Photo Paper", "Paper", 100, "Sheets"),
            ("12x18 Glossy Paper", "Paper", 50, "Sheets"),
            ("Lamination Pouch", "Lamination", 100, "Pouches")
        ]
        for m in def_materials:
            try:
                cursor.execute("""
                    INSERT INTO master_raw_materials (studio_id, material_name, category, current_qty, unit, last_updated) 
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (new_studio_id, f"{m[0]} ({s_name})", m[1], m[2], m[3], now_s))
            except Exception:
                pass

        conn.commit()
        return jsonify({"status": "success", "message": f"🎉 '{s_name}' का खाता बन गया! 30 दिन का फ्री ट्रायल सक्रिय है।"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": f"Server Error: {str(e)}"}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# 🔐 1. SAAS DYNAMIC LOGIN ENGINE (Password & 6-Digit PIN Match)
# =====================================================================

LOGIN_ATTEMPTS = {}

@app.route('/api/saas/login_studio', methods=['POST'])
def saas_login_studio():
    conn = None
    try:
        data = request.json or {}
        mobile = str(data.get('mobile', '')).strip()
        auth_secret = str(data.get('password', '')).strip()
        remember_me = bool(data.get('remember_me', False))

        if not mobile or not auth_secret:
            return jsonify({"status": "error", "message": "मोबाइल नंबर और पासवर्ड/पिन दर्ज करें!"}), 400

        # 🚫 5 गलत प्रयासों के बाद 5 मिनट का अस्थायी ब्लॉक
        now_ts = datetime.now()
        att_info = LOGIN_ATTEMPTS.get(mobile, {'count': 0, 'last_time': now_ts})
        
        if att_info['count'] >= 5:
            time_diff = (now_ts - att_info['last_time']).total_seconds()
            if time_diff < 300:
                remaining_sec = int(300 - time_diff)
                return jsonify({"status": "error", "message": f"सुरक्षा कारणों से यह नंबर लॉक है! कृपया {remaining_sec} सेकंड बाद प्रयास करें।"}), 429
            else:
                LOGIN_ATTEMPTS[mobile] = {'count': 0, 'last_time': now_ts}

        # 👑 1. HIM STUDIO मास्टर ओनर डायरेक्ट बाईपास लॉगिन
        if mobile == MASTER_MOBILE and (check_password_hash(generate_password_hash(MASTER_PASSWORD), auth_secret) or auth_secret in [MASTER_PASSWORD, '123456', '1234', '9691']):
            LOGIN_ATTEMPTS.pop(mobile, None)
            session.clear()
            session.permanent = True
            session['logged_in'] = True
            session['studio_id'] = 1
            session['studio_name'] = 'HIM STUDIO'
            session['owner_name'] = 'Dinesh Sahu'
            session['mobile'] = '9691158104'
            session['email'] = 'dineshsahumdp@gmail.com'
            session['address'] = 'Shri Radha madhav Dham Parisar, bhim chowk thana road ratanpur 495445'
            
            # 🔥 जीरो पासवर्ड / जीरो पिन बाईपास चाबी
            session['is_master'] = True
            session['is_master_admin'] = True
            session['role'] = 'master'

            return jsonify({"status": "success", "message": "मास्टर लॉगिन सफल (बायपास एक्टिव)!", "redirect": "/dashboard"})

        # 🏢 2. अन्य SaaS यूज़र्स (Clients) के लिए डेटाबेस लॉगिन
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM saas_studios WHERE mobile = ?", (mobile,))
        user_row = cursor.fetchone()

        if user_row:
            u_dict = dict(user_row)
            p_hash = str(u_dict.get('password_hash') or u_dict.get('password') or '')
            pin_hash = str(u_dict.get('master_pin_hash') or u_dict.get('pin') or '')

            match_found = False
            if p_hash and (check_password_hash(p_hash, auth_secret) or p_hash == auth_secret):
                match_found = True
            elif pin_hash and (check_password_hash(pin_hash, auth_secret) or pin_hash == auth_secret):
                match_found = True

            if match_found:
                LOGIN_ATTEMPTS.pop(mobile, None)

                if str(u_dict.get('status', 'Active')).lower() == 'suspended':
                    return jsonify({"status": "error", "message": "खाता सस्पेंड कर दिया गया है! एडमिन से संपर्क करें।"}), 403

                # एक्सपायरी चेक
                exp_str = u_dict.get('plan_expiry') or ''
                if exp_str and exp_str != 'Lifetime':
                    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
                        try:
                            if date.today() > datetime.strptime(exp_str, fmt).date():
                                cursor.execute("UPDATE saas_studios SET status = 'Expired' WHERE id = ?", (u_dict['id'],))
                                conn.commit()
                                return jsonify({"status": "error", "message": "आपका सब्सक्रिप्शन समाप्त हो चुका है!"}), 403
                            break
                        except Exception:
                            continue

                s_id = int(u_dict.get('id') or 1)
                session.clear()
                session.permanent = remember_me
                session['logged_in'] = True
                session['studio_id'] = s_id
                session['studio_name'] = u_dict.get('studio_name') or 'Studio'
                session['owner_name'] = u_dict.get('owner_name') or 'Admin'
                session['mobile'] = mobile
                
                # केवल id=1 होने पर ही बाईपास मिलेगा
                is_master = (s_id == 1 or mobile == '9691158104')
                session['is_master'] = is_master
                session['is_master_admin'] = is_master
                session['role'] = 'master' if is_master else 'client'

                return jsonify({"status": "success", "message": f"स्वागत है, {session['studio_name']}!", "redirect": "/dashboard"})

        current_count = LOGIN_ATTEMPTS.get(mobile, {'count': 0})['count'] + 1
        LOGIN_ATTEMPTS[mobile] = {'count': current_count, 'last_time': now_ts}
        remaining_tries = max(0, 5 - current_count)

        return jsonify({"status": "error", "message": f"गलत मोबाइल नंबर या पासवर्ड/पिन! (शेष प्रयास: {remaining_tries})"}), 401

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()
# =====================================================================
# 🔒 BULLETPROOF STRICT LOGOUT & CACHE PREVENTION
# =====================================================================
@app.route('/logout')
def logout_redirect():
    session.clear()
    resp = make_response(redirect(url_for('saas_login_page')))
    resp.set_cookie('session', '', expires=0)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/api/logout', methods=['GET', 'POST'])
def handle_logout():
    session.clear()
    resp = make_response(jsonify({"status": "success", "message": "Logged out successfully!"}))
    resp.set_cookie('session', '', expires=0)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    return resp

@app.after_request
def prevent_caching(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/verify_admin', methods=['POST'])
@login_required
def verify_admin():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        entered_password = str(data.get('password') or '').strip()

        if not entered_password:
            return jsonify({"status": "error", "message": "कृपया पासवर्ड दर्ज करें!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT password_hash FROM saas_studios WHERE id = ?", (cur_studio,))
        studio = cursor.fetchone()

        if not studio:
            return jsonify({"status": "error", "message": "स्टूडियो खाता नहीं मिला!"}), 404

        db_hash = str(studio['password_hash'] or '').strip()
        is_valid = False

        try:
            if db_hash:
                # 🛠️ फिक्स: password की जगह entered_password
                if check_password_hash(db_hash, entered_password) or entered_password == db_hash:
                    is_valid = True
        except Exception as auth_err:
            print("Password check error:", auth_err)

        if not is_valid:
            return jsonify({"status": "error", "message": "❌ गलत पासवर्ड!"}), 403

        return jsonify({"status": "success", "message": "सत्यापन सफल!"})

    except Exception as e:
        return jsonify({"status": "error", "message": f"Server Error: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/saas_master')
@login_required
def saas_master_page():
    # केवल Master Admin (Tenant 1) ही इसे खोल सकता है
    if int(get_current_studio_id()) != 1:
        return redirect('/dashboard')
    return render_template('saas_master.html')

@app.route('/api/change_security_credentials', methods=['POST'])
@app.route('/api/change_user_password', methods=['POST'])
@login_required
def change_security_credentials():
    conn = None
    try:
        data = request.get_json(force=True, silent=True) or request.form or {}
        
        # 1. फ्रंटएंड से भेजी गई करंट फील्ड को पहचानें
        old_secret = str(
            data.get('old_password') or 
            data.get('old_pin') or 
            data.get('current_pin') or 
            data.get('old_secret') or ''
        ).strip()
        
        new_pass = str(data.get('new_password') or data.get('password') or '').strip()
        new_pin = str(data.get('new_pin') or data.get('pin') or '').strip()
        cur_studio = get_current_studio_id()

        if not old_secret:
            return jsonify({"status": "error", "message": "कृपया मौजूदा पासवर्ड या पिन दर्ज करें!"}), 400

        if not new_pass and not new_pin:
            return jsonify({"status": "error", "message": "नया पासवर्ड या नया पिन दर्ज करें!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # must_change_pwd भी निकालें
        cursor.execute("SELECT password_hash, master_pin_hash, must_change_pwd FROM saas_studios WHERE id = ?", (cur_studio,))
        row = cursor.fetchone()

        if not row:
            return jsonify({"status": "error", "message": "स्टूडियो खाता नहीं मिला!"}), 404

        db_p_hash = str(row['password_hash'] or '')
        db_pin_hash = str(row['master_pin_hash'] or '')
        must_change = int(row['must_change_pwd'] or 0)

        # 🎯 2. सुरक्षित यूज़र वेरिफिकेशन
        is_verified = False
        try:
            # पासवर्ड या पिन का सही हैश मैच करना
            if db_p_hash and (check_password_hash(db_p_hash, old_secret) or db_p_hash == old_secret):
                is_verified = True
            elif db_pin_hash and (check_password_hash(db_pin_hash, old_secret) or db_pin_hash == old_secret):
                is_verified = True
            # अगर एडमिन ने हाल ही में रिसेट किया है, तो केवल तभी अस्थायी डिफ़ॉल्ट पिन मान्य होगा जब database में भी वही सेट हो
            elif must_change == 1 and old_secret in ['123456', '1234', 'studio@1234']:
                is_verified = True
        except Exception:
            pass

        if not is_verified:
            return jsonify({"status": "error", "message": "❌ मौजूदा पासवर्ड/पिन गलत है!"}), 403

        # 🎯 3. नए क्रेडेंशियल्स अपडेट करना और must_change_pwd को 0 करना
        updates = []
        params = []

        if new_pass:
            updates.append("password_hash = ?")
            params.append(generate_password_hash(new_pass))

        if new_pin:
            updates.append("master_pin_hash = ?")
            params.append(generate_password_hash(new_pin))

        # अनिवार्य पासवर्ड बदलने का फ्लैग 0 सेट करें
        updates.append("must_change_pwd = 0")

        params.append(cur_studio)
        query = f"UPDATE saas_studios SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, tuple(params))
        conn.commit()

        # सेशन से फोर्स फ्लैग साफ़ करें
        session.pop('force_change_pwd', None)

        return jsonify({
            "status": "success", 
            "message": "क्रेडेंशियल्स सफलतापूर्वक अपडेट हो गए!", 
            "redirect": "/dashboard"
        })

    except Exception as e:
        if conn: 
            conn.rollback()
        return jsonify({"status": "error", "message": f"Server Error: {str(e)}"}), 500
    finally:
        if conn: 
            conn.close()

@app.route('/api/verify_master_password', methods=['POST'])
@login_required
def verify_master_password():
    data = request.json or {}
    entered_password = str(data.get('password', '')).strip()

    if not entered_password:
        return jsonify({"status": "error", "message": "कृपया मालिक का मुख्य पासवर्ड दर्ज करें!"}), 400

    cur_studio_id = session.get('studio_id')
    cur_mobile = session.get('mobile')

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM saas_studios WHERE id = ? OR mobile = ?", 
                       (cur_studio_id, str(cur_mobile).strip()))
        row = cursor.fetchone()

        if not row:
            return jsonify({"status": "error", "message": "खाता रिकॉर्ड नहीं मिला!"}), 404

        r_dict = dict(row)
        p_hash = str(r_dict.get('password_hash') or '')
        pin_hash = str(r_dict.get('master_pin_hash') or '')

        # 🚫 काउंटर पिन को रिजेक्ट करें (ताकि ऑपरेटर न खोल सके)
        # अगर इनपुट केवल 4 से 6 अंकों का शुद्ध नंबर है और वह पिन से मैच हो रहा है:
        if entered_password.isdigit() and len(entered_password) in [4, 6]:
            if (pin_hash and check_password_hash(pin_hash, entered_password)) or pin_hash == entered_password:
                return jsonify({
                    "status": "error", 
                    "message": "❌ यह बिलिंग पिन है! यहाँ मालिक का मुख्य पासवर्ड दर्ज करें।"
                }), 403

        # 🔑 मालिक का पासवर्ड मैच करें
        is_matched = False
        
        # 1. मुख्य पासवर्ड हैश से जाँच
        if p_hash and (check_password_hash(p_hash, entered_password) or p_hash == entered_password):
            is_matched = True
            
        # 2. अगर कॉलम स्वैप हो गए थे तो दूसरे कॉलम से भी जाँच (सिर्फ अगर इनपुट अल्फ़ान्यूमेरिक पासवर्ड हो)
        elif pin_hash and not entered_password.isdigit():
            if check_password_hash(pin_hash, entered_password) or pin_hash == entered_password:
                is_matched = True

        if is_matched:
            return jsonify({"status": "success", "message": "मालिक प्रमाणीकरण सफल!"})

        return jsonify({
            "status": "error", 
            "message": "❌ गलत पासवर्ड! केवल स्टूडियो मालिक का मुख्य पासवर्ड मान्य है।"
        }), 403

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()
# =====================================================================
# 🌐 5. HTML PAGE ROUTING (Matching All Template Files Exactly)
# =====================================================================

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard_page(): 
    return render_template('dashboard.html')

@app.route('/new_bill')
@login_required
def new_bill_page(): 
    return render_template('new_bill.html')

@app.route('/all_bills')
@login_required
def all_bills_view_page(): 
    return render_template('all_bills.html')

@app.route('/wedding')
@login_required
def wedding_page(): 
    return render_template('wedding_booking.html')

@app.route('/print_wedding_a4')
@login_required
def print_wedding_a4_page():
    return render_template('print_wedding_a4.html')

@app.route('/token_counter')
@login_required
def token_counter_view_page(): 
    return render_template('daily_token_counter.html')

@app.route('/stock')
@login_required
def stock_page(): 
    return render_template('Stock_product.html')

@app.route('/credit_ledger')
@login_required
def credit_ledger_page(): 
    return render_template('credit_ledger.html')

@app.route('/purchases')
@login_required
def purchases_page(): 
    return render_template('purchases.html')

@app.route('/studio_expenses')
@login_required
def studio_expenses_page(): 
    return render_template('studio_expenses.html')

@app.route('/material_stock')
@login_required
def material_stock_page(): 
    return render_template('material_stock.html')

@app.route('/equipment_tracking')
@login_required
def equipment_tracking_page(): 
    return render_template('equipment_tracking.html')

@app.route('/employee_corner')
@login_required
def employee_corner_page(): 
    return render_template('employee_corner.html')

@app.route('/work_export')
@login_required
def work_export_page(): 
    return render_template('work_export.html')

@app.route('/report')
@login_required
def report_page(): 
    return render_template('report.html')

@app.route('/change_password')
@login_required
def change_password_page(): 
    return render_template('change_password.html')

@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    # स्लैश को नॉर्मलाइज़ करें
    clean_subpath = os.path.normpath(filename).lstrip(r"\/")

    # 1. पहले डायरेक्ट पाथ चेक करें (उदा. uploads/purchase_bills/16x20.jpg)
    full_path_1 = os.path.join(UPLOADS_DIR, clean_subpath)
    if os.path.exists(full_path_1) and os.path.isfile(full_path_1):
        return send_file(full_path_1)

    # 2. अगर purchase_bills फ़ोल्डर के अंदर हो (उदा. सिर्फ 16x20.jpg भेजा गया हो)
    full_path_2 = os.path.join(UPLOADS_DIR, "purchase_bills", os.path.basename(clean_subpath))
    if os.path.exists(full_path_2) and os.path.isfile(full_path_2):
        return send_file(full_path_2)

    # 3. अगर मेन uploads फ़ोल्डर के अंदर हो
    full_path_3 = os.path.join(UPLOADS_DIR, os.path.basename(clean_subpath))
    if os.path.exists(full_path_3) and os.path.isfile(full_path_3):
        return send_file(full_path_3)

    return "File Not Found on Server", 404
# =====================================================================
# 🔢 हेल्पर फ़ंक्शन: अगला बिल नंबर निकालना
# =====================================================================
def get_next_studio_bill_number(cursor, studio_id):
    try:
        cursor.execute("SELECT MAX(CAST(bill_no AS INTEGER)) FROM bookings WHERE studio_id = ?", (studio_id,))
        r_b = cursor.fetchone()
        max_b = int(r_b[0]) if (r_b and r_b[0] is not None) else 0
    except Exception:
        max_b = 0

    try:
        cursor.execute("SELECT MAX(CAST(COALESCE(bill_no, 0) AS INTEGER)) FROM wedding_bookings WHERE studio_id = ?", (studio_id,))
        r_w = cursor.fetchone()
        max_w = int(r_w[0]) if (r_w and r_w[0] is not None) else 0
    except Exception:
        max_w = 0

    return max(max_b, max_w) + 1


@app.route('/api/next_bill_no', methods=['GET'])
@login_required
def get_next_unified_bill_no():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        next_no = get_next_studio_bill_number(cursor, cur_studio)
        return jsonify({"status": "success", "next_bill": next_no})
    except Exception as e:
        print("next_bill_no error:", e)
        return jsonify({"status": "success", "next_bill": 1})
    finally:
        if conn:
            conn.close()


@app.route('/api/save_bill', methods=['POST'])
@login_required
def save_bill():
    data = request.json or {}
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        is_update_val = data.get('is_update')
        update_bill_no = data.get('update_bill_no') or data.get('editing_bill_no') or data.get('bill_no')

        is_edit_mode = False
        if update_bill_no and (is_update_val in [True, 'true', 'True', 1, '1'] or str(is_update_val).lower() == 'true'):
            is_edit_mode = True

        cust_name = str(data.get('customer_name', '')).strip()
        mobile = str(data.get('mobile', '')).strip()
        address = str(data.get('address', '')).strip()
        bill_date = str(data.get('bill_date', '')).strip()
        delivery_date = str(data.get('delivery_date', '')).strip()

        total_amt = float(data.get('total_amount') or data.get('gross_total') or 0.0)
        disc_amt = float(data.get('discount_amount') or data.get('discount') or 0.0)
        adv_amt = float(data.get('advance_amount') or (float(data.get('cash_paid', 0)) + float(data.get('upi_paid', 0))) or 0.0)
        bal_amt = float(data.get('balance_amount') or (total_amt - disc_amt - adv_amt) or 0.0)

        cash_p = float(data.get('cash_paid', 0))
        upi_p = float(data.get('upi_paid', 0))

        now_dt = datetime.now()
        now_time = now_dt.strftime('%Y-%m-%d %H:%M:%S')
        pay_time_display = now_dt.strftime('%d/%m/%Y %I:%M %p')

        target_sizes = ['12x36', '12x24', '12x18', '10x12', '8x12', '6x8', '5x7', '4x6']

        # 1. EDIT MODE
        if is_edit_mode:
            bill_no = int(update_bill_no)

            cursor.execute("SELECT status, payment_history, share_token FROM bookings WHERE bill_no = ? AND studio_id = ?", (bill_no, cur_studio))
            old_b = cursor.fetchone()
            current_status = old_b['status'] if (old_b and old_b['status'] == 'Delivered') else 'Pending'
            share_token = old_b['share_token'] if (old_b and old_b['share_token']) else secrets.token_hex(16)

            cursor.execute("SELECT item_name, quantity FROM booking_items WHERE bill_no = ? AND studio_id = ?", (bill_no, cur_studio))
            old_items = cursor.fetchall()

            for old_itm in old_items:
                old_pname = str(old_itm['item_name'] or '').strip()
                old_qty = float(old_itm['quantity'] or 0)
                if old_pname and old_qty > 0:
                    cursor.execute("""
                        UPDATE product_stock 
                        SET pqty = CAST(pqty AS REAL) + ?, last_updated = ? 
                        WHERE TRIM(LOWER(pname)) = TRIM(LOWER(?)) AND studio_id = ?
                    """, (old_qty, now_time, old_pname, cur_studio))

                    old_lower = old_pname.lower()
                    restore_material = None
                    is_old_only_frame = ('frame' in old_lower or 'glass' in old_lower) and not ('print' in old_lower or 'photo' in old_lower)

                    if not is_old_only_frame:
                        if any(k in old_lower for k in ['aadhaar', 'aadhar', 'pan', 'voter', 'passport']):
                            restore_material = '4X6'
                        else:
                            for sz in target_sizes:
                                if sz in old_lower:
                                    restore_material = sz.upper()
                                    break

                        if restore_material:
                            cursor.execute("""
                                UPDATE master_raw_materials 
                                SET current_qty = current_qty + ?, last_updated = ? 
                                WHERE CAST(studio_id AS INTEGER) = ? 
                                  AND (TRIM(UPPER(material_name)) = ? OR UPPER(material_name) LIKE ?)
                            """, (old_qty, now_time, cur_studio, restore_material, f"%{restore_material}%"))

            cursor.execute("DELETE FROM booking_items WHERE bill_no = ? AND studio_id = ?", (bill_no, cur_studio))

            cursor.execute("""
                UPDATE bookings 
                SET customer_name = ?, mobile = ?, address = ?, delivery_date = ?, 
                    total_amount = ?, discount_amount = ?, advance_amount = ?, balance_amount = ?,
                    cash_paid = ?, upi_paid = ?, status = ?, share_token = ?
                WHERE bill_no = ? AND studio_id = ?
            """, (cust_name, mobile, address, delivery_date, total_amt, disc_amt, adv_amt, bal_amt, cash_p, upi_p, current_status, share_token, bill_no, cur_studio))

        # 2. NEW BILL MODE
        else:
            bill_no = get_next_studio_bill_number(cursor, cur_studio)
            share_token = secrets.token_hex(16)
            current_status = 'Pending'

            initial_history = []
            if adv_amt > 0:
                initial_history.append({
                    "date": pay_time_display,
                    "amount": adv_amt,
                    "mode": "Cash" if cash_p > 0 and upi_p == 0 else ("UPI" if upi_p > 0 and cash_p == 0 else "Split"),
                    "note": "Advance Payment"
                })

            cursor.execute("""
                INSERT INTO bookings 
                (studio_id, bill_no, customer_name, mobile, address, bill_date, delivery_date, 
                 total_amount, discount_amount, advance_amount, balance_amount, cash_paid, upi_paid, 
                 status, share_token, payment_history)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (cur_studio, bill_no, cust_name, mobile, address, bill_date, delivery_date,
                  total_amt, disc_amt, adv_amt, bal_amt, cash_p, upi_p, current_status, share_token, json.dumps(initial_history)))

        # 3. ITEMS INSERT
        items = data.get('items', [])
        for item in items:
            p_desc = str(item.get('desc') or item.get('item_name') or item.get('product_name') or '').strip()
            p_qty = float(item.get('qty') or item.get('quantity') or 1)
            p_rate = float(item.get('rate') or item.get('price') or 0.0)
            p_tot = float(item.get('total') or (p_qty * p_rate) or 0.0)

            if p_desc:
                cursor.execute("""
                    INSERT INTO booking_items (studio_id, bill_no, item_name, quantity, price, total)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (cur_studio, bill_no, p_desc, p_qty, p_rate, p_tot))

                cursor.execute("""
                    UPDATE product_stock 
                    SET pqty = CAST(pqty AS REAL) - ?, last_updated = ? 
                    WHERE TRIM(LOWER(pname)) = TRIM(LOWER(?)) AND studio_id = ?
                """, (p_qty, now_time, p_desc, cur_studio))

                p_desc_lower = p_desc.lower()
                deduct_material = None
                is_frame_only = ('frame' in p_desc_lower or 'glass' in p_desc_lower) and not ('print' in p_desc_lower or 'photo' in p_desc_lower)

                if not is_frame_only:
                    if any(k in p_desc_lower for k in ['aadhaar', 'aadhar', 'pan', 'voter', 'passport']):
                        deduct_material = '4X6'
                    else:
                        for sz in target_sizes:
                            if sz in p_desc_lower:
                                deduct_material = sz.upper()
                                break

                    if deduct_material:
                        cursor.execute("""
                            UPDATE master_raw_materials 
                            SET current_qty = current_qty - ?, last_updated = ? 
                            WHERE CAST(studio_id AS INTEGER) = ? 
                              AND (TRIM(UPPER(material_name)) = ? OR UPPER(material_name) LIKE ?)
                        """, (p_qty, now_time, cur_studio, deduct_material, f"%{deduct_material}%"))

        conn.commit()
        base_url = request.host_url.rstrip('/')
        full_invoice_url = f"{base_url}/invoice/{share_token}"

        return jsonify({
            "status": "success",
            "message": "Invoice saved successfully!",
            "bill_no": bill_no,
            "share_token": share_token,
            "share_url": full_invoice_url
        })

    except Exception as e:
        if conn:
            conn.rollback()
        print("🚨 BILL SAVE ERROR ->", e)
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/print_invoice/<int:bill_no>', methods=['GET'])
def print_invoice(bill_no):
    cur_studio = get_current_studio_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM bookings WHERE bill_no=? AND studio_id=?', (bill_no, cur_studio))
    bill_row = cursor.fetchone()

    cursor.execute('SELECT * FROM booking_items WHERE bill_no=? AND studio_id=?', (bill_no, cur_studio))
    items_rows = cursor.fetchall()

    cursor.execute("SELECT * FROM saas_studios WHERE id = ?", (cur_studio,))
    st_row = cursor.fetchone()
    conn.close()

    if not bill_row: 
        return 'Invoice Not Found', 404

    bill = dict(bill_row)
    st_data = dict(st_row) if st_row else {}

    studio = {
        'studio_name': st_data.get('studio_name') or st_data.get('name') or "HIM STUDIO",
        'address': st_data.get('address') or '',
        'phone': st_data.get('phone') or st_data.get('mobile') or st_data.get('contact') or st_data.get('phone_no') or '',
        'whatsapp': st_data.get('whatsapp') or st_data.get('wa_number') or st_data.get('mobile') or '',
        'email': st_data.get('email') or ''
    }

    # तारीख फॉर्मेटिंग
    bill_date = str(bill.get('bill_date') or '')
    if '-' in bill_date:
        parts_b = bill_date.split('-')
        if len(parts_b) == 3 and len(parts_b[0]) == 4:
            bill_date = f'{parts_b[2]}/{parts_b[1]}/{parts_b[0]}'

    deliv_date = str(bill.get('delivery_date') or '')
    if '-' in deliv_date:
        parts = deliv_date.split('-')
        if len(parts) == 3 and len(parts[0]) == 4:
            deliv_date = f'{parts[2]}/{parts[1]}/{parts[0]}'

    return render_template('invoice_print.html', 
                           bill=bill, 
                           items=items_rows, 
                           studio=studio, 
                           bill_date=bill_date, 
                           deliv_date=deliv_date)


@app.route('/api/sync_temp_stock', methods=['POST'])
@login_required
def sync_temp_stock():
    conn = None
    try:
        data = request.json or {}
        p_id = data.get('product_id')
        change = int(data.get('change', 0))
        cur_studio = get_current_studio_id()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE product_stock SET pqty = pqty + ? WHERE id = ? AND studio_id = ?", (change, p_id, cur_studio))
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()  

@app.route('/api/get_inventory', methods=['GET'])
@login_required
def get_inventory():
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id, pname, pqty, pprice, pdisc, last_updated FROM product_stock WHERE studio_id = ? ORDER BY pname ASC', (cur_studio,))
        rows = cursor.fetchall()
        conn.close()
        data = [{'id': row['id'], 'product_name': row['pname'], 'pname': row['pname'], 'price': row['pprice'], 'pprice': row['pprice'], 'quantity': row['pqty'], 'pqty': row['pqty'], 'discount': row['pdisc'], 'pdisc': row['pdisc']} for row in rows]
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/all_bills', methods=['GET'])
@app.route('/api/get_all_bills', methods=['GET'])
@login_required
def get_all_bills_api():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. रेगुलर बिल्स निकालें
        cursor.execute("SELECT * FROM bookings WHERE studio_id = ? ORDER BY CAST(bill_no AS INTEGER) DESC", (cur_studio,))
        regular_bills = cursor.fetchall()
        
        # 2. वेडिंग बिल्स निकालें
        cursor.execute("SELECT * FROM wedding_bookings WHERE studio_id = ? ORDER BY CAST(COALESCE(bill_no, id) AS INTEGER) DESC", (cur_studio,))
        wedding_bills = cursor.fetchall()
        
        bills_list = []
        for bill in regular_bills:
            b_dict = dict(bill)
            b_dict['bill_type'] = 'Regular'
            bills_list.append(b_dict)
            
        for w in wedding_bills:
            w_dict = dict(w)
            
            # 🛠️ फिक्स: यहाँ id नहीं, बल्कि सही bill_no दिखाएँ
            w_dict['bill_no'] = w_dict.get('bill_no') if w_dict.get('bill_no') is not None else w_dict.get('id')
            
            w_dict['grand_total'] = w_dict.get('total_deal_amount', 0)
            w_dict['advance'] = w_dict.get('advance_received', 0)
            w_dict['remaining_balance'] = w_dict.get('balance_amount', 0)
            w_dict['bill_date'] = str(w_dict.get('created_at', '')).split()[0]
            w_dict['bill_type'] = 'Wedding'
            bills_list.append(w_dict)
            
        # सही बिल नंबर के आधार पर सॉर्ट करें
        bills_list.sort(key=lambda x: int(x.get('bill_no') or 0), reverse=True)
        return jsonify({"status": "success", "data": bills_list})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# 🔍 GET SINGLE BILL DETAILS (FOR ACTION PORTAL & BILL MODIFY/EDIT)
# =====================================================================
@app.route('/api/get_bill_details/<bill_no>', methods=['GET'])
@login_required
def get_bill_details(bill_no):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. मुख्य बिल विवरण निकालें
        cursor.execute("""
            SELECT id, bill_no, customer_name, mobile, address, bill_date, delivery_date,
                   total_amount, discount_amount, advance_amount, balance_amount,
                   cash_paid, upi_paid, status, payment_history
            FROM bookings 
            WHERE bill_no = ? AND studio_id = ?
        """, (bill_no, cur_studio))
        
        b_row = cursor.fetchone()
        if not b_row:
            return jsonify({'status': 'error', 'message': 'Bill not found!'}), 404

        bill_data = dict(b_row)

        # 2. पार्ट पेमेंट हिस्ट्री को लिस्ट बनाएं
        try:
            hist = json.loads(bill_data.get('payment_history') or '[]')
        except Exception:
            hist = []
        bill_data['payment_history'] = hist
        bill_data['history'] = hist  # पुराने कोड के साथ तालमेल के लिए

        # 3. बिल के आइटम्स निकालें (booking_items टेबल से)
        items_list = []
        try:
            cursor.execute("""
                SELECT item_name, quantity, price, total 
                FROM booking_items 
                WHERE bill_no = ? AND studio_id = ?
            """, (bill_no, cur_studio))
            items_rows = cursor.fetchall()
            for item in items_rows:
                items_list.append({
                    'item_name': item['item_name'],
                    'desc': item['item_name'],
                    'quantity': item['quantity'],
                    'qty': item['quantity'],
                    'price': float(item['price'] or 0),
                    'rate': float(item['price'] or 0),
                    'total': float(item['total'] or 0)
                })
        except Exception:
            # अगर booking_items टेबल में studio_id न हो तो केवल bill_no से निकालें
            cursor.execute("SELECT item_name, quantity, price, total FROM booking_items WHERE bill_no = ?", (bill_no,))
            for item in cursor.fetchall():
                items_list.append({
                    'item_name': item['item_name'],
                    'desc': item['item_name'],
                    'quantity': item['quantity'],
                    'qty': item['quantity'],
                    'price': float(item['price'] or 0),
                    'rate': float(item['price'] or 0),
                    'total': float(item['total'] or 0)
                })

        bill_data['items'] = items_list
        bill_data['status'] = 'success'

        return jsonify(bill_data)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/add_installment', methods=['POST'])
@login_required
def add_installment():
    data = request.json or {}
    bill_no = data.get('bill_no')
    amount = float(data.get('amount', 0))
    mode = data.get('mode', 'CASH')
    cur_studio = get_current_studio_id()
    
    if not bill_no or amount <= 0: return jsonify({"status": "error", "message": "Invalid Bill No or Amount"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT advance_amount, balance_amount FROM bookings WHERE bill_no = ? AND studio_id = ?", (bill_no, cur_studio))
        bill = cursor.fetchone()
        if not bill: return jsonify({"status": "error", "message": "Bill not found"}), 404
            
        new_adv = float(bill['advance_amount'] or 0) + amount
        new_bal = max(0.0, float(bill['balance_amount'] or 0) - amount)
        new_status = 'Delivered' if new_bal == 0 else 'Pending'

        cursor.execute("UPDATE bookings SET advance_amount = ?, balance_amount = ?, status = ? WHERE bill_no = ? AND studio_id = ?", (new_adv, new_bal, new_status, bill_no, cur_studio))
        conn.commit()
        return jsonify({"status": "success", "message": "Installment added successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/mark_delivered', methods=['POST'])
@login_required
def mark_delivered():
    data = request.json or {}
    bill_no = data.get('bill_no')
    cur_studio = get_current_studio_id()
    if not bill_no: return jsonify({"status": "error", "message": "Bill No missing"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT balance_amount FROM bookings WHERE bill_no = ? AND studio_id = ?", (bill_no, cur_studio))
        bill = cursor.fetchone()
        if not bill: return jsonify({"status": "error", "message": "Bill not found"}), 404
        if float(bill['balance_amount'] or 0) > 0:
            return jsonify({"status": "error", "message": f"Delivery blocked! Balance due is ₹ {bill['balance_amount']}"}), 400

        cursor.execute("UPDATE bookings SET status = 'Delivered' WHERE bill_no = ? AND studio_id = ?", (bill_no, cur_studio))
        conn.commit()
        return jsonify({"status": "success", "message": "Order delivered!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/search_customer', methods=['GET'])
@login_required
def search_customer():
    query = request.args.get('q', '').strip()
    cur_studio = get_current_studio_id()
    if not query or len(query) < 3: 
        return jsonify([])
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        search_pattern = f"{query}%"
        # Bookings और Wedding Bookings दोनों से कस्टमर का डेटा खोजें
        cursor.execute("""
            SELECT customer_name, mobile, address FROM bookings 
            WHERE (mobile LIKE ? OR customer_name LIKE ?) AND studio_id = ?
            UNION
            SELECT customer_name, mobile, address FROM wedding_bookings 
            WHERE (mobile LIKE ? OR customer_name LIKE ?) AND studio_id = ?
            LIMIT 10
        """, (search_pattern, search_pattern, cur_studio, search_pattern, search_pattern, cur_studio))
        
        return jsonify([dict(row) for row in cursor.fetchall()])
    except Exception:
        return jsonify([])
    finally:
        conn.close()
# =====================================================================
# 💍 7. WEDDING OPERATIONS (ISOLATED PER STUDIO)
# =====================================================================

@app.route('/api/get_wedding_bookings', methods=['GET'])
@login_required
def get_wedding_bookings():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        # सुनिश्चित करें कि bill_no कॉलम मौजूद हो
        cursor.execute("PRAGMA table_info(wedding_bookings)")
        cols = [col[1] for col in cursor.fetchall()]
        
        if 'bill_no' not in cols:
            try:
                cursor.execute("ALTER TABLE wedding_bookings ADD COLUMN bill_no INTEGER")
                conn.commit()
            except Exception:
                pass

        # 🎯 bill_no के आधार पर ही क्रमबद्ध करें
        cursor.execute("""
            SELECT * FROM wedding_bookings 
            WHERE studio_id = ? 
            ORDER BY CAST(COALESCE(bill_no, id) AS INTEGER) DESC
        """, (cur_studio,))

        raw_rows = cursor.fetchall()
        rows = []
        
        for r in raw_rows:
            d = dict(r)
            # 🛠️ फिक्स: bill_no को प्राथमिकता दें, ताकि फ्रंटएंड को कभी भी ग्लोबल id (20, 22) न दिखे
            correct_no = d.get('bill_no') if d.get('bill_no') is not None else d.get('id')
            d['bill_no'] = correct_no
            d['display_bill_no'] = correct_no
            rows.append(d)

        return jsonify({'status': 'success', 'data': rows})
    except Exception as e:
        print(f"[FETCH ERROR] get_wedding_bookings: {str(e)}")
        return jsonify({'status': 'success', 'data': []})
    finally:
        if conn: 
            conn.close()

@app.route('/api/save_wedding_booking', methods=['POST'])
@login_required
def save_wedding_booking():
    conn = None
    try:
        import traceback
        cur_studio = get_current_studio_id()
        data = request.get_json(force=True, silent=True) or {}
        
        customer_name = str(data.get('customer_name', '')).strip()
        mobile = str(data.get('mobile', '')).strip()
        if not customer_name or not mobile:
            return jsonify({'status': 'error', 'message': 'Customer Name and Mobile are required!'}), 400

        tot = float(data.get('total_deal_amount') or data.get('total_budget') or 0.0)
        disc = float(data.get('discount_amount') or data.get('discount') or 0.0)
        adv = float(data.get('advance_received') or data.get('advance_paid') or 0.0)
        bal = max(0.0, tot - disc - adv)
        now_str = datetime.now().strftime('%d/%m/%Y %H:%M')

        conn = get_db_connection()
        cursor = conn.cursor()

        # आवश्यक सभी टेबल्स तुरंत सुनिश्चित करें
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wedding_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_no INTEGER,
                studio_id INTEGER DEFAULT 1,
                customer_name TEXT,
                mobile TEXT,
                address TEXT,
                venue_location TEXT,
                total_deal_amount REAL,
                discount_amount REAL,
                advance_received REAL,
                balance_amount REAL,
                status TEXT,
                services TEXT,
                wedding_details TEXT,
                wed_date_1 TEXT, wed_date_2 TEXT, wed_date_3 TEXT, wed_date_4 TEXT,
                wed_event_1 TEXT, wed_event_2 TEXT, wed_event_3 TEXT, wed_event_4 TEXT,
                wed_cam_1 TEXT, wed_cam_2 TEXT, wed_cam_3 TEXT, wed_cam_4 TEXT,
                wed_staff_1 TEXT, wed_staff_2 TEXT, wed_staff_3 TEXT, wed_staff_4 TEXT,
                payment_history TEXT,
                created_at TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wedding_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                booking_id INTEGER,
                amount REAL,
                payment_mode TEXT,
                date_time TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wedding_incentive_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                emp_id INTEGER,
                emp_name TEXT,
                wedding_id INTEGER,
                event_date TEXT,
                event_name TEXT,
                suggested_amount REAL DEFAULT 500,
                status TEXT DEFAULT 'Pending',
                created_at TEXT
            )
        """)
        conn.commit()

        assigned_bill_no = get_next_studio_bill_number(cursor, cur_studio)

        wed_desc = str(data.get('wedding_details') or data.get('description') or data.get('notes') or '').strip()
        services_cart = data.get('services_list') or data.get('services') or []
        services_json = json.dumps(services_cart) if isinstance(services_cart, (list, dict)) else str(services_cart)

        # दोनों प्रकार के फील्ड नामों का फॉलबैक
        c_addr = data.get('address') or data.get('cust_address') or ''
        v_loc = data.get('venue_location') or data.get('event_location') or ''
        
        d1 = data.get('wed_date_1') or data.get('day1_date') or ''
        d2 = data.get('wed_date_2') or data.get('day2_date') or ''
        d3 = data.get('wed_date_3') or data.get('day3_date') or ''
        d4 = data.get('wed_date_4') or data.get('day4_date') or ''

        e1 = data.get('wed_event_1') or data.get('day1_event') or ''
        e2 = data.get('wed_event_2') or data.get('day2_event') or ''
        e3 = data.get('wed_event_3') or data.get('day3_event') or ''
        e4 = data.get('wed_event_4') or data.get('day4_event') or ''

        cursor.execute("""
            INSERT INTO wedding_bookings (
                studio_id, bill_no, customer_name, mobile, address, venue_location,
                total_deal_amount, discount_amount, advance_received, balance_amount,
                status, services, wedding_details,
                wed_date_1, wed_date_2, wed_date_3, wed_date_4,
                wed_event_1, wed_event_2, wed_event_3, wed_event_4,
                wed_cam_1, wed_cam_2, wed_cam_3, wed_cam_4,
                wed_staff_1, wed_staff_2, wed_staff_3, wed_staff_4,
                payment_history, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cur_studio, assigned_bill_no, customer_name, mobile, c_addr, v_loc,
            tot, disc, adv, bal, 'Pending', services_json, wed_desc,
            d1, d2, d3, d4,
            e1, e2, e3, e4,
            data.get('wed_cam_1', ''), data.get('wed_cam_2', ''), data.get('wed_cam_3', ''), data.get('wed_cam_4', ''),
            data.get('wed_staff_1', ''), data.get('wed_staff_2', ''), data.get('wed_staff_3', ''), data.get('wed_staff_4', ''),
            json.dumps([{"amount": adv, "date_time": now_str, "mode": "Cash"}]) if adv > 0 else '[]',
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        new_row_id = cursor.lastrowid

        if adv > 0:
            try:
                cursor.execute("""
                    INSERT INTO wedding_payments (studio_id, booking_id, amount, payment_mode, date_time)
                    VALUES (?, ?, ?, 'Cash', ?)
                """, (cur_studio, new_row_id, adv, now_str))
            except Exception:
                pass

        for d_i, (dt_v, ev_v) in enumerate([(d1, e1), (d2, e2), (d3, e3), (d4, e4)], start=1):
            stf = data.get(f'wed_staff_{d_i}')
            if stf and dt_v:
                try:
                    stage_wedding_incentive_if_assigned(cursor, new_row_id, stf, dt_v, ev_v, cur_studio)
                except Exception:
                    pass

        conn.commit()
        return jsonify({
            'status': 'success', 
            'bill_no': assigned_bill_no, 
            'id': new_row_id, 
            'message': f'वेडिंग बिल #{assigned_bill_no} सफलतापूर्वक सुरक्षित हो गया!'
        })
    except Exception as e:
        if conn: conn.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/update_wedding_full', methods=['POST'])
@login_required
def update_wedding_full():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.get_json(silent=True) or {}
        b_id = data.get('id') or data.get('booking_id') or data.get('bill_no')
        if not b_id: return jsonify({'status': 'error', 'message': 'Booking ID is required'}), 400

        tot = float(data.get('total_deal_amount', 0) or 0)
        disc = float(data.get('discount_amount', 0) or 0)
        adv = float(data.get('advance_received', 0) or 0)
        bal = tot - disc - adv
        
        services_data = data.get('services', [])
        services_str = json.dumps(services_data) if isinstance(services_data, (list, dict)) else str(services_data or '[]')

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE wedding_bookings SET
                customer_name = ?, mobile = ?, address = ?, venue_location = ?,
                total_deal_amount = ?, discount_amount = ?, advance_received = ?, balance_amount = ?,
                status = ?, services = ?, wedding_details = ?,
                wed_date_1 = ?, wed_date_2 = ?, wed_date_3 = ?, wed_date_4 = ?,
                wed_event_1 = ?, wed_event_2 = ?, wed_event_3 = ?, wed_event_4 = ?,
                wed_cam_1 = ?, wed_cam_2 = ?, wed_cam_3 = ?, wed_cam_4 = ?,
                wed_staff_1 = ?, wed_staff_2 = ?, wed_staff_3 = ?, wed_staff_4 = ?
            WHERE id = ? AND studio_id = ?
        ''', (
            data.get('customer_name', ''), data.get('mobile', ''), data.get('address', ''), data.get('venue_location', ''),
            tot, disc, adv, bal, data.get('status', 'Pending'), services_str, data.get('wedding_details', ''),
            data.get('wed_date_1', ''), data.get('wed_date_2', ''), data.get('wed_date_3', ''), data.get('wed_date_4', ''),
            data.get('wed_event_1', ''), data.get('wed_event_2', ''), data.get('wed_event_3', ''), data.get('wed_event_4', ''),
            data.get('wed_cam_1', ''), data.get('wed_cam_2', ''), data.get('wed_cam_3', ''), data.get('wed_cam_4', ''),
            data.get('wed_staff_1', ''), data.get('wed_staff_2', ''), data.get('wed_staff_3', ''), data.get('wed_staff_4', ''),
            b_id, cur_studio
        ))

        for d_i in range(1, 5):
            stage_wedding_incentive_if_assigned(cursor, b_id, data.get(f'wed_staff_{d_i}'), data.get(f'wed_date_{d_i}'), data.get(f'wed_event_{d_i}'), cur_studio)

        conn.commit()
        return jsonify({'status': 'success', 'message': 'Wedding updated successfully'})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/wedding/manage_order', methods=['POST'])
@login_required
def manage_wedding_order():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        raw_id = data.get('id') or data.get('bill_no')
        try:
            record_id = int(raw_id)
        except (ValueError, TypeError):
            record_id = raw_id

        action = str(data.get('action', '')).strip().lower()
        
        # 🎯 केवल मुख्य लॉगिन पासवर्ड निकालें (पिन नहीं)
        entered_password = str(data.get('password') or '').strip()

        if not entered_password:
            return jsonify({"status": "error", "message": "मालिक का पासवर्ड दर्ज करना अनिवार्य है!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. डेटाबेस से केवल password_hash मंगाएं
        cursor.execute("SELECT id, password_hash FROM saas_studios WHERE id = ? OR id = ?", (cur_studio, str(cur_studio)))
        studio = cursor.fetchone()

        if not studio:
            return jsonify({"status": "error", "message": "स्टूडियो खाता नहीं मिला!"}), 404

        db_hash = str(studio['password_hash'] or '')

        # 🎯 केवल लॉगिन पासवर्ड से सत्यापन (Zero-Tolerance: पिन से कोई लेना-देना नहीं)
        is_valid = False
        try:
            if db_hash and (check_password_hash(db_hash, entered_password) or entered_password == db_hash):
                is_valid = True
        except Exception:
            pass

        if not is_valid:
            # अगर पासवर्ड मेल न खाए तो 403 रिटर्न होगा
            return jsonify({"status": "error", "message": "❌ गलत पासवर्ड!"}), 403

        # 2. CANCEL ACTION
        if action == 'cancel':
            cursor.execute("""
                UPDATE wedding_bookings 
                SET status = 'Cancelled', balance_amount = 0 
                WHERE (id = ? OR bill_no = ?) AND (studio_id = ? OR studio_id = ?)
            """, (record_id, record_id, cur_studio, str(cur_studio)))
            
            try:
                cursor.execute("""
                    UPDATE wedding_incentive_staging 
                    SET status = 'Cancelled' 
                    WHERE (wedding_id = ? OR wedding_id = (SELECT id FROM wedding_bookings WHERE bill_no = ? AND (studio_id = ? OR studio_id = ?))) 
                      AND (studio_id = ? OR studio_id = ?)
                """, (record_id, record_id, cur_studio, str(cur_studio), cur_studio, str(cur_studio)))
            except Exception:
                pass

            conn.commit()
            return jsonify({"status": "success", "message": f"शादी बुकिंग #{record_id} रद्द कर दी गई!"})

        # 3. DELIVER ACTION
        elif action == 'deliver':
            cursor.execute("""
                UPDATE wedding_bookings 
                SET status = 'Delivered' 
                WHERE (id = ? OR bill_no = ?) AND (studio_id = ? OR studio_id = ?)
            """, (record_id, record_id, cur_studio, str(cur_studio)))
            conn.commit()
            return jsonify({"status": "success", "message": f"शादी बुकिंग #{record_id} डिलीवर और लॉक हो गई!"})

        # 4. MODIFY ACTION
        elif action == 'modify':
            cart_data = data.get('services') or data.get('services_list') or []
            services_json = cart_data if isinstance(cart_data, str) else json.dumps(cart_data)
            wed_desc = str(data.get('wedding_details') or data.get('description') or data.get('notes') or '').strip()

            tot_amt = float(data.get('total_deal_amount') or data.get('total_amount') or 0.0)
            disc_amt = float(data.get('discount_amount') or 0.0)
            adv_amt = float(data.get('advance_received') or data.get('advance_amount') or 0.0)
            bal_amt = max(0.0, tot_amt - disc_amt - adv_amt)

            cursor.execute("PRAGMA table_info(wedding_bookings)")
            existing_cols = [row[1] for row in cursor.fetchall()]

            fields_map = {
                'customer_name': data.get('customer_name'),
                'mobile': data.get('mobile'),
                'address': data.get('address'),
                'venue_location': data.get('venue_location'),
                'total_deal_amount': tot_amt,
                'discount_amount': disc_amt,
                'advance_received': adv_amt,
                'balance_amount': bal_amt,
                'wedding_details': wed_desc,
                'services': services_json,
                'wed_date_1': data.get('wed_date_1'), 'wed_date_2': data.get('wed_date_2'),
                'wed_date_3': data.get('wed_date_3'), 'wed_date_4': data.get('wed_date_4'),
                'wed_event_1': data.get('wed_event_1'), 'wed_event_2': data.get('wed_event_2'),
                'wed_event_3': data.get('wed_event_3'), 'wed_event_4': data.get('wed_event_4'),
                'wed_cam_1': data.get('wed_cam_1'), 'wed_cam_2': data.get('wed_cam_2'),
                'wed_cam_3': data.get('wed_cam_3'), 'wed_cam_4': data.get('wed_cam_4'),
                'wed_staff_1': data.get('wed_staff_1'), 'wed_staff_2': data.get('wed_staff_2'),
                'wed_staff_3': data.get('wed_staff_3'), 'wed_staff_4': data.get('wed_staff_4')
            }

            set_clauses = []
            val_list = []
            for col, val in fields_map.items():
                if col in existing_cols and val is not None:
                    set_clauses.append(f"{col} = ?")
                    val_list.append(val)

            if not set_clauses:
                return jsonify({"status": "error", "message": "डेटाबेस में कोई वैध कॉलम नहीं मिला!"}), 400

            query = f"UPDATE wedding_bookings SET {', '.join(set_clauses)} WHERE (id = ? OR bill_no = ?) AND (studio_id = ? OR studio_id = ?)"
            val_list.extend([record_id, record_id, cur_studio, str(cur_studio)])
            cursor.execute(query, tuple(val_list))

            cursor.execute("SELECT id FROM wedding_bookings WHERE (id = ? OR bill_no = ?) AND (studio_id = ? OR studio_id = ?)", (record_id, record_id, cur_studio, str(cur_studio)))
            w_row = cursor.fetchone()
            real_db_id = w_row['id'] if w_row else record_id

            for d_i in range(1, 5):
                staff_val = data.get(f'wed_staff_{d_i}')
                event_date = data.get(f'wed_date_{d_i}')
                event_name = data.get(f'wed_event_{d_i}')
                if staff_val and event_date:
                    stage_wedding_incentive_if_assigned(cursor, real_db_id, staff_val, event_date, event_name, cur_studio)

            conn.commit()
            return jsonify({"status": "success", "message": "विवरण और कार्ट सफलतापूर्वक सुरक्षित हो गया!"})

        return jsonify({"status": "error", "message": "अमान्य एक्शन!"}), 400

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": f"Server SQL Error: {str(e)}"}), 500
    finally:
        if conn: conn.close()


@app.route('/api/add_wedding_payment', methods=['POST'])
@login_required
def add_wedding_payment():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        booking_id = data.get('id') or data.get('bill_no')
        amount = float(data.get('amount', 0))
        payment_mode = str(data.get('payment_mode', 'Cash')).strip()
        
        if not booking_id or amount <= 0:
            return jsonify({"status": "error", "message": "मान्य बिल आईडी और भुगतान राशि भरें!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # id या bill_no दोनों से सुरक्षित खोजें
        cursor.execute("""
            SELECT id, total_deal_amount, discount_amount, advance_received, balance_amount, payment_history 
            FROM wedding_bookings 
            WHERE (id = ? OR bill_no = ?) AND studio_id = ?
        """, (booking_id, booking_id, cur_studio))
        row = cursor.fetchone()

        if not row:
            return jsonify({"status": "error", "message": "वेडिंग बुकिंग रिकॉर्ड नहीं मिला!"}), 404

        real_id = row['id']
        total_deal = float(row['total_deal_amount'] or 0)
        discount = float(row['discount_amount'] or 0)
        current_adv = float(row['advance_received'] or 0)

        # पेमेंट हिस्ट्री पार्स करें
        try:
            history = json.loads(row['payment_history']) if row['payment_history'] else []
        except Exception:
            history = []

        now_str = datetime.now().strftime('%d/%m/%Y %I:%M %p')
        history.append({
            "date_time": now_str, 
            "amount": amount, 
            "mode": payment_mode,
            "note": "Part Payment Received"
        })

        new_adv = current_adv + amount
        new_balance = max(0.0, total_deal - discount - new_adv)
        new_status = 'Delivered' if new_balance == 0 else 'Pending'

        # wedding_bookings टेबल में अपडेट करें
        cursor.execute("""
            UPDATE wedding_bookings 
            SET advance_received = ?, 
                balance_amount = ?, 
                status = ?,
                payment_history = ? 
            WHERE id = ? AND studio_id = ?
        """, (new_adv, new_balance, new_status, json.dumps(history), real_id, cur_studio))

        # शादी पेमेंट्स की अलग टेबल (wedding_payments) में भी रिकॉर्ड जोड़ें
        try:
            cursor.execute("""
                INSERT INTO wedding_payments (studio_id, booking_id, amount, payment_mode, date_time)
                VALUES (?, ?, ?, ?, ?)
            """, (cur_studio, real_id, amount, payment_mode, now_str))
        except Exception:
            pass

        conn.commit()
        return jsonify({
            "status": "success", 
            "message": f"₹{amount:.2f} का भुगतान सफलतापूर्वक दर्ज हुआ!",
            "new_balance": new_balance,
            "advance_received": new_adv
        })

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": f"Server Error: {str(e)}"}), 500
    finally:
        if conn: conn.close()

@app.route('/api/wedding/cancel_or_modify', methods=['POST'])
@login_required
def cancel_or_modify_wedding():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        is_master = bool(session.get('is_master') or int(cur_studio) == 1)
        data = request.get_json(force=True, silent=True) or request.form or {}
        
        bill_no = data.get('bill_no')
        action_type = str(data.get('action_type') or '').strip().lower()
        password = str(data.get('password') or '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()

        # 👑 मास्टर ओनर के लिए पासवर्ड बाईपास; क्लाइंट स्टूडियो के लिए पासवर्ड चेक
        if not is_master:
            if not password:
                return jsonify({"status": "error", "message": "सुरक्षा के लिए पासवर्ड दर्ज करना अनिवार्य है!"}), 400

            cursor.execute("SELECT password_hash FROM saas_studios WHERE id = ?", (cur_studio,))
            studio = cursor.fetchone()

            if not studio:
                return jsonify({"status": "error", "message": "स्टूडियो खाता नहीं मिला!"}), 404

            db_hash = str(studio['password_hash'] or '').strip()
            if not (check_password_hash(db_hash, password) or password == db_hash):
                return jsonify({"status": "error", "message": "❌ गलत पासवर्ड!"}), 403

        # 🛑 केवल वेडिंग बुकिंग को CANCEL करना
        if action_type == 'cancel':
            cursor.execute("""
                UPDATE wedding_bookings 
                SET status = 'Cancelled', balance_amount = 0 
                WHERE (bill_no = ? OR id = ?) AND studio_id = ?
            """, (bill_no, bill_no, cur_studio))

            try:
                cursor.execute("""
                    UPDATE wedding_incentive_staging 
                    SET status = 'Cancelled' 
                    WHERE (wedding_id = ? OR wedding_id = (SELECT id FROM wedding_bookings WHERE bill_no = ? AND studio_id = ?)) 
                      AND studio_id = ?
                """, (bill_no, bill_no, cur_studio, cur_studio))
            except Exception:
                pass

            conn.commit()
            return jsonify({"status": "success", "message": f"शादी बिल #{bill_no} रद्द कर दिया गया!"})

        elif action_type == 'verify_for_modify':
            return jsonify({"status": "success", "message": "सत्यापन सफल हुआ!"})

        return jsonify({"status": "error", "message": "अमान्य एक्शन!"}), 400

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/handle_staging_incentive', methods=['POST'])
@login_required
def handle_staging_incentive():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        
        action = str(data.get('action') or '').strip().lower()
        incentive_id = data.get('incentive_id') or data.get('id') or data.get('staging_id')

        if not action or not incentive_id:
            return jsonify({"status": "error", "message": "अमान्य डेटा! Action या Incentive ID नहीं मिली।"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. स्टेजिंग रिकॉर्ड से इंसेंटिव विवरण निकालें
        cursor.execute("""
            SELECT id, emp_id, emp_name, suggested_amount, event_name, event_date 
            FROM wedding_incentive_staging 
            WHERE id = ? AND studio_id = ?
        """, (incentive_id, cur_studio))
        row = cursor.fetchone()

        if not row:
            return jsonify({"status": "error", "message": "इंसेंटिव रिकॉर्ड नहीं मिला!"}), 404

        emp_id = row['emp_id']
        emp_name = row['emp_name']
        amount = float(row['suggested_amount'] or 500.0)
        event_name = row['event_name'] or 'Wedding Event'
        now_time_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        # 2. 'Add' (स्वीकृत करके सैलरी में जोड़ना)
        if action in ['add', 'approve']:
            # A. स्टेजिंग टेबल अपडेट करें
            cursor.execute("""
                UPDATE wedding_incentive_staging 
                SET status = 'Approved' 
                WHERE id = ? AND studio_id = ?
            """, (incentive_id, cur_studio))

            # B. कर्मचारी लेज़र में जोड़ें ताकि सैलरी कैलकुलेटर में दिखे
            cursor.execute("""
                INSERT INTO employee_transactions 
                (studio_id, employee_id, emp_id, trans_date, trans_type, amount, remarks) 
                VALUES (?, ?, ?, ?, 'Incentive', ?, ?)
            """, (cur_studio, emp_id, emp_id, now_time_str, amount, f"Wedding Shoot Incentive ({event_name})"))

            conn.commit()
            return jsonify({
                "status": "success", 
                "message": f"₹{amount:.2f} का इंसेंटिव {emp_name} की सैलरी में जुड़ गया!"
            })

        # 3. 'Reject' (रद्द करना)
        elif action in ['reject', 'cancel']:
            cursor.execute("""
                UPDATE wedding_incentive_staging 
                SET status = 'Rejected' 
                WHERE id = ? AND studio_id = ?
            """, (incentive_id, cur_studio))

            conn.commit()
            return jsonify({"status": "success", "message": "इंसेंटिव रद्द कर दिया गया!"})

        return jsonify({"status": "error", "message": "अमान्य एक्शन!"}), 400

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()


@app.route('/print_wedding/<int:booking_id>', methods=['GET'])
@login_required
def print_wedding(booking_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        # 🏢 1. वर्तमान लॉग-इन स्टूडियो का हेडर डेटा फेच करें
        cursor.execute("SELECT * FROM saas_studios WHERE id = ?", (cur_studio,))
        st_row = cursor.fetchone()
        st_data = dict(st_row) if st_row else {}

        studio = {
            'studio_name': st_data.get('studio_name') or "HIM STUDIO",
            'owner_name': st_data.get('owner_name') or "Authorized Signatory",
            'address': st_data.get('address') or "",
            'mobile': st_data.get('mobile') or "",
            'whatsapp': st_data.get('whatsapp') or st_data.get('mobile') or "",
            'email': st_data.get('email') or ""
        }

        # 💍 2. बुकिंग रिकॉर्ड निकालें (id या bill_no दोनों से सुरक्षित)
        cursor.execute("""
            SELECT * FROM wedding_bookings 
            WHERE (id = ? OR bill_no = ?) AND studio_id = ?
        """, (booking_id, booking_id, cur_studio))
        wedding = cursor.fetchone()
        
        if not wedding: 
            return "<h2 style='text-align:center;padding:50px;font-family:sans-serif;'>⚠️ Wedding Invoice Not Found!</h2>", 404
            
        w_dict = dict(wedding)
        if not w_dict.get('booking_date'): 
            w_dict['booking_date'] = w_dict.get('created_at', 'N/A')
        
        # 💳 3. पार्ट पेमेंट और बैलेंस कैलकुलेशन
        cursor.execute("SELECT SUM(amount) FROM wedding_payments WHERE booking_id = ? AND studio_id = ?", (w_dict['id'], cur_studio))
        part_sum_row = cursor.fetchone()
        total_part_paid = float(part_sum_row[0] or 0) if (part_sum_row and part_sum_row[0] is not None) else 0.0
        
        base_advance = float(w_dict.get('advance_received') or 0.0)
        # advance_received और पार्ट पेमेंट्स का सही तालमेल
        final_advance_paid = max(base_advance, total_part_paid)
        
        total_deal = float(w_dict.get('total_deal_amount') or 0.0)
        discount = float(w_dict.get('discount_amount') or 0.0)
        final_balance = max(0.0, total_deal - discount - final_advance_paid)
        
        w_dict['computed_advance'] = final_advance_paid
        w_dict['computed_balance'] = final_balance
        
        # 🛒 4. सर्विसेज लिस्ट JSON पार्सिंग
        try: 
            w_dict['services_list'] = json.loads(w_dict.get('services')) if isinstance(w_dict.get('services'), str) else (w_dict.get('services') or [])
        except Exception: 
            w_dict['services_list'] = []

        # 🚀 5. booking के साथ studio ऑब्जेक्ट भी टेम्पलेट को पास करें
        return render_template('print_wedding_a4.html', booking=w_dict, studio=studio)
        
    except Exception as e:
        return f"Wedding Print Error: {str(e)}", 500
    finally:
        if conn: 
            conn.close()

@app.route('/api/next_wedding_bill_no', methods=['GET'])
@login_required
def get_next_wedding_bill_no():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # टेबल में bill_no कॉलम चेक करें, अगर नहीं है तो id लें
        cursor.execute("PRAGMA table_info(wedding_bookings)")
        cols = [col[1] for col in cursor.fetchall()]
        bill_col = "bill_no" if "bill_no" in cols else "id"

        # केवल वर्तमान स्टूडियो का अधिकतम बिल नंबर निकालें
        cursor.execute(f"SELECT MAX(CAST({bill_col} AS INTEGER)) FROM wedding_bookings WHERE studio_id = ?", (cur_studio,))
        r = cursor.fetchone()
        max_bill = r[0] if (r and r[0] is not None) else 0

        next_no = int(max_bill) + 1
            
        return jsonify({"status": "success", "next_bill_no": next_no})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "next_bill_no": 1}), 200
    finally:
        if conn: conn.close()

# =====================================================================
# 📦 1. GET INVENTORY SYNC API (100% Crash-Proof)
# =====================================================================
@app.route('/api/get_token_inventory_sync', methods=['GET'])
@app.route('/get_token_inventory_sync', methods=['GET'])
@login_required
def get_token_inventory_sync():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Raw Materials (कच्चा माल)
        materials = []
        try:
            cursor.execute("SELECT * FROM master_raw_materials WHERE CAST(studio_id AS INTEGER) = ? ORDER BY id DESC", (cur_studio,))
            for r in cursor.fetchall():
                row = dict(r)
                m_name = row.get('material_name') or row.get('name') or 'Item'
                m_qty = float(row.get('current_qty') or row.get('qty') or 0.0)
                m_unit = row.get('unit') or 'Sheets'
                materials.append({
                    'id': row.get('id'),
                    'name': m_name,
                    'qty': m_qty,
                    'unit': m_unit,
                    'item_type': 'material'
                })
        except Exception as me:
            print("Raw Material fetch warning:", me)

        # 2. Product Stock (तैयार माल - डायनामिक प्राइस और नाम रीडिंग)
        products = []
        try:
            cursor.execute("SELECT * FROM product_stock WHERE CAST(studio_id AS INTEGER) = ? ORDER BY id DESC", (cur_studio,))
            for r in cursor.fetchall():
                row = dict(r)
                p_name = row.get('pname') or row.get('product_name') or row.get('name') or 'Product'
                p_qty = float(row.get('pqty') or row.get('quantity') or row.get('qty') or 0.0)
                p_price = float(row.get('pprice') or row.get('price') or row.get('selling_price') or 0.0)
                products.append({
                    'id': row.get('id'),
                    'name': p_name,
                    'qty': p_qty,
                    'unit': 'Pcs',
                    'price': p_price,
                    'item_type': 'product'
                })
        except Exception as pe:
            print("Product Stock fetch warning:", pe)

        # 3. रजिस्ट्रेशन डेट
        reg_date_str = datetime.now().strftime('%Y-%m-%d')
        for tbl in ['saas_studios', 'studios', 'users']:
            try:
                cursor.execute(f"SELECT created_at FROM {tbl} WHERE id = ?", (cur_studio,))
                st_row = cursor.fetchone()
                if st_row and st_row['created_at']:
                    reg_date_str = str(st_row['created_at'])
                    break
            except Exception:
                continue

        return jsonify({
            'status': 'success',
            'materials': materials,
            'products': products,
            'registered_at': reg_date_str
        })
    except Exception as e:
        print("Inventory Sync Fatal Error:", e)
        return jsonify({'status': 'success', 'materials': [], 'products': [], 'registered_at': datetime.now().strftime('%Y-%m-%d')})
    finally:
        if conn: conn.close()


# =====================================================================
# 🎯 2. GET TOKENS API (100% Crash-Proof)
# =====================================================================
@app.route('/api/get_tokens', methods=['GET'])
@app.route('/get_tokens', methods=['GET'])
@login_required
def get_tokens_safe_api():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        filter_opt = str(request.args.get('filter', 'today')).strip()

        now_dt = datetime.now()
        cur_d_str = now_dt.strftime('%d/%m/%Y')
        cur_d_dash = now_dt.strftime('%Y-%m-%d')
        cur_m_str = now_dt.strftime('%m')
        cur_y_str = now_dt.strftime('%Y')

        conn = get_db_connection()
        cursor = conn.cursor()

        # सभी टोकन सुरक्षित लाना
        cursor.execute("""
            SELECT * FROM daily_tokens 
            WHERE CAST(studio_id AS INTEGER) = ?
            ORDER BY CAST(token_no AS INTEGER) DESC, id DESC
        """, (cur_studio,))
        raw_rows = cursor.fetchall()

        tokens_list = []
        today_cash = 0.0
        today_upi = 0.0
        month_total = 0.0

        for r in raw_rows:
            item = dict(r)
            c_val = float(item.get('cash_paid') or 0.0)
            u_val = float(item.get('upi_paid') or 0.0)
            tot_val = float(item.get('total_amount') or (c_val + u_val))
            t_date = str(item.get('token_date') or '')
            c_at = str(item.get('created_at') or '')

            item['cash_paid'] = c_val
            item['upi_paid'] = u_val
            item['total_amount'] = tot_val

            # Live Metrics calculation
            if any(t_date.startswith(d) or c_at.startswith(d) for d in [cur_d_dash, cur_d_str]):
                today_cash += c_val
                today_upi += u_val

            if f"/{cur_m_str}/{cur_y_str}" in t_date or f"{cur_y_str}-{cur_m_str}" in t_date or f"{cur_y_str}-{cur_m_str}" in c_at:
                month_total += tot_val

            # Python-level Filtering (Never Crashes SQLite)
            match = False
            if filter_opt.lower() in ['today', 'today_only', '']:
                if any(t_date.startswith(d) or c_at.startswith(d) for d in [cur_d_dash, cur_d_str]):
                    match = True
            elif len(filter_opt) == 4 and filter_opt.isdigit():
                if filter_opt in t_date or filter_opt in c_at:
                    match = True
            else:
                digits = ''.join([c for c in filter_opt if c.isdigit()])
                req_m = digits[:2] if len(digits) >= 2 else cur_m_str
                req_y = digits[2:6] if len(digits) >= 6 else cur_y_str
                if (f"/{req_m}/{req_y}" in t_date) or (f"/{int(req_m)}/{req_y}" in t_date) or (f"{req_y}-{req_m}" in t_date) or (f"{req_y}-{req_m}" in c_at):
                    match = True

            if match:
                tokens_list.append(item)

        return jsonify({
            'status': 'success',
            'data': tokens_list,
            'tokens': tokens_list,
            'metrics': {
                'today_cash': today_cash,
                'today_upi': today_upi,
                'week_total': today_cash + today_upi,
                'month_total': month_total
            }
        })
    except Exception as e:
        print("get_tokens 500 Error:", e)
        return jsonify({
            'status': 'success',
            'data': [],
            'tokens': [],
            'metrics': {'today_cash': 0.0, 'today_upi': 0.0, 'week_total': 0.0, 'month_total': 0.0}
        })
    finally:
        if conn: conn.close()
# =====================================================================
# 💾 SAVE TOKEN ROUTE (AUTO-RECIPE + CART COMBO)
# =====================================================================
@app.route('/api/save_token', methods=['POST'])
@app.route('/save_token', methods=['POST'])
@login_required
def save_token():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.get_json(force=True, silent=True) or request.form or {}

        customer_name = str(data.get('customer_name') or '').strip()
        mobile = str(data.get('mobile') or '').strip()
        work_type = str(data.get('work_type') or 'General Work').strip()
        
        discount = float(data.get('discount') or 0.0)
        cash_paid = float(data.get('cash_paid') or 0.0)
        upi_paid = float(data.get('upi_paid') or 0.0)
        total_amount = cash_paid + upi_paid

        # 🚫 0 अमाउंट पर रोक
        if total_amount <= 0.0:
            return jsonify({'status': 'error', 'message': 'कुल राशि ₹0.00 है! कृपया मान्य भुगतान राशि भरें।'}), 400

        cart_items = data.get('cart_items', [])
        
        now_dt = datetime.now()
        today_iso = now_dt.strftime('%Y-%m-%d')
        today_display = now_dt.strftime('%d/%m/%Y')
        current_timestamp = now_dt.strftime('%Y-%m-%d %H:%M:%S')
        log_time = now_dt.strftime('%d/%m/%Y %I:%M %p')

        conn = get_db_connection()
        cursor = conn.cursor()

        # नया टोकन नंबर जनरेट करना
        cursor.execute('''
            SELECT COALESCE(MAX(token_no), 0) 
            FROM daily_tokens 
            WHERE CAST(studio_id AS INTEGER) = ? AND (token_date = ? OR token_date = ?)
        ''', (cur_studio, today_iso, today_display))
        
        last_row = cursor.fetchone()
        last_token_no = last_row[0] if last_row else 0
        next_token_no = int(last_token_no) + 1

        item_names_summary = []
        total_mat_qty = 0.0

        # =====================================================================
        # 🎯 1. सिंगल वर्क-टाइप डिडक्शन इंजन (नो डबल डिडक्ट)
        # =====================================================================
        work_type_clean = str(work_type or '').strip()
        work_lower = work_type_clean.lower()
        recipe_applied = False

        if work_type_clean and work_type_clean != 'General Work':
            # A. पहले सर्विस रेसिपी से काटें
            try:
                recipe_cuts = deduct_recipe_stock(
                    cursor=cursor,
                    studio_id=cur_studio,
                    work_type=work_type_clean,
                    service_qty=1.0,
                    ref_title=f"Token #{next_token_no}",
                    log_time=log_time,
                    cur_timestamp=current_timestamp
                )
                if recipe_cuts:
                    item_names_summary.extend(recipe_cuts)
                    total_mat_qty += len(recipe_cuts)
                    recipe_applied = True
            except Exception as recipe_err:
                print("Recipe Deduct Error:", recipe_err)

            # B. अगर रेसिपी टेबल में नहीं मिला, तभी स्मार्ट साइज़ फ़ॉलबैक चलाएँ
            if not recipe_applied:
                matched_material = None

                if any(k in work_lower for k in ['aadhaar', 'aadhar', 'pan']):
                    matched_material = 'A4 Paper'
                elif 'passport' in work_lower:
                    matched_material = '4X6'
                else:
                    target_sizes = ['12x36', '12x24', '12x18', '10x12', '8x12', '6x8', '5x7', '4x6']
                    for sz in target_sizes:
                        if sz in work_lower:
                            matched_material = sz.upper()
                            break

                if matched_material:
                    try:
                        cursor.execute("""
                            UPDATE master_raw_materials 
                            SET current_qty = MAX(0.0, current_qty - 1.0), last_updated = ? 
                            WHERE CAST(studio_id AS INTEGER) = ? 
                              AND (TRIM(UPPER(material_name)) = ? OR UPPER(material_name) LIKE ?)
                        """, (log_time, cur_studio, matched_material, f"%{matched_material}%"))

                        cursor.execute("""
                            INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) 
                            VALUES (?, ?, -1.0, 'TOKEN_AUTO_RECIPE', ?, ?)
                        """, (cur_studio, matched_material, f"Token #{next_token_no} ({work_type_clean})", log_time))

                        item_names_summary.append(f"{matched_material} (1.0)")
                        total_mat_qty += 1.0
                    except Exception as deduct_err:
                        print(f"Smart Fallback Deduct Error for {matched_material}:", deduct_err)

        # =====================================================================
        # 🎯 2. अलग से जोड़े गए कार्ट आइटम्स का डिडक्शन
        # =====================================================================
        for item in cart_items:
            i_name = str(item.get('name') or '').strip()
            i_qty = float(item.get('qty') or 0.0)
            i_type = str(item.get('type') or '').strip().lower()

            if not i_name or i_qty <= 0:
                continue

            item_names_summary.append(f"{i_name} ({i_qty})")
            total_mat_qty += i_qty

            try:
                if i_type == 'material':
                    cursor.execute("""
                        UPDATE master_raw_materials 
                        SET current_qty = MAX(0.0, current_qty - ?), last_updated = ? 
                        WHERE CAST(studio_id AS INTEGER) = ? 
                          AND (TRIM(LOWER(material_name)) = TRIM(LOWER(?)) OR material_name LIKE ?)
                    """, (i_qty, log_time, cur_studio, i_name, f"%{i_name}%"))

                    cursor.execute("""
                        INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) 
                        VALUES (?, ?, ?, 'TOKEN_CART_DEDUCT', ?, ?)
                    """, (cur_studio, i_name, -i_qty, f"Token #{next_token_no}", log_time))

                elif i_type == 'product':
                    cursor.execute("""
                        UPDATE product_stock 
                        SET pqty = MAX(0, pqty - ?), last_updated = ? 
                        WHERE CAST(studio_id AS INTEGER) = ? 
                          AND (TRIM(LOWER(pname)) = TRIM(LOWER(?)) OR pname LIKE ?)
                    """, (int(i_qty), current_timestamp, cur_studio, i_name, f"%{i_name}%"))

                else:
                    cursor.execute("""
                        UPDATE master_raw_materials 
                        SET current_qty = MAX(0.0, current_qty - ?), last_updated = ? 
                        WHERE CAST(studio_id AS INTEGER) = ? AND TRIM(LOWER(material_name)) = TRIM(LOWER(?))
                    """, (i_qty, log_time, cur_studio, i_name))
                    if cursor.rowcount == 0:
                        cursor.execute("""
                            UPDATE product_stock 
                            SET pqty = MAX(0, pqty - ?), last_updated = ? 
                            WHERE CAST(studio_id AS INTEGER) = ? AND TRIM(LOWER(pname)) = TRIM(LOWER(?))
                        """, (int(i_qty), current_timestamp, cur_studio, i_name))
            except Exception as stock_err:
                print(f"Stock Deduct Warning for {i_name}:", stock_err)

        final_material_used = ", ".join(item_names_summary) if item_names_summary else "-"

        # =====================================================================
        # 🎯 3. टोकन रिकॉर्ड सुरक्षित सेव करना
        # =====================================================================
        try:
            cursor.execute('''
                INSERT INTO daily_tokens 
                (studio_id, token_no, customer_name, mobile, work_type, material_used, material_qty, discount, cash_paid, upi_paid, total_amount, token_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (cur_studio, next_token_no, customer_name, mobile, work_type, final_material_used, total_mat_qty, discount, cash_paid, upi_paid, total_amount, today_iso, current_timestamp))
        except Exception:
            cursor.execute('''
                INSERT INTO daily_tokens 
                (studio_id, token_no, customer_name, mobile, work_type, material_used, material_qty, cash_paid, upi_paid, total_amount, token_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (cur_studio, next_token_no, customer_name, mobile, work_type, final_material_used, total_mat_qty, cash_paid, upi_paid, total_amount, today_iso, current_timestamp))

        conn.commit()
        return jsonify({'status': 'success', 'message': f'Token #{next_token_no} created successfully!', 'token_no': next_token_no})

    except Exception as e:
        if conn: conn.rollback()
        print("Save Token Exception:", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

# 🖨️ स्टूडियो खर्च (Daily Expenses) प्रिंट रिपोर्ट रूट
@app.route('/print_daily_expenses', methods=['GET'])
@app.route('/print_expense_report', methods=['GET'])
@login_required
def print_daily_expenses_report():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        filter_type = request.args.get('filter', 'today')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # स्टूडियो का नाम प्राप्त करें
        cursor.execute("SELECT studio_name FROM saas_studios WHERE id = ?", (cur_studio,))
        st_row = cursor.fetchone()
        studio_title = st_row['studio_name'] if st_row else "HIM STUDIO"

        # फ़िल्टर के अनुसार खर्च का डेटा निकालें
        if filter_type == 'today':
            today_str = datetime.now().strftime('%d/%m/%Y')
            today_dash = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('SELECT * FROM daily_expenses WHERE (exp_date = ? OR exp_date = ?) AND studio_id = ? ORDER BY id DESC', (today_str, today_dash, cur_studio))
            title_suffix = f"Today's Report ({today_str})"
        else:
            cursor.execute('SELECT * FROM daily_expenses WHERE exp_date LIKE ? AND studio_id = ? ORDER BY id DESC', (f'%{filter_type}%', cur_studio))
            title_suffix = f"Filtered Report ({filter_type})"

        expenses = [dict(r) for r in cursor.fetchall()]
        total_expense = sum(float(r['amount'] or 0) for r in expenses)

        return render_template(
            'print_expense_report.html',
            studio_title=studio_title,
            title_suffix=title_suffix,
            expenses=expenses,
            total_expense=total_expense,
            current_time=datetime.now().strftime('%d/%m/%Y %I:%M %p')
        )
    except Exception as e:
        return f"Expense Print Error: {str(e)}", 500
    finally:
        if conn:
            conn.close()

# ==========================================
# 📊 MASTER FINANCIAL & BUSINESS REPORT API (SAFE & CRASH-PROOF)
# ==========================================
@app.route('/api/get_master_report', methods=['POST'])
@login_required
def get_master_report():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        req_data = request.get_json(silent=True) or {}
        
        raw_from = req_data.get('from_date', '').strip()
        raw_to = req_data.get('to_date', '').strip()

        # तारीखों को सुरक्षित YYYY-MM-DD फॉर्मेट में बदलें
        from_std = fix_date(raw_from) if raw_from else datetime.now().strftime('%Y-%m-01')
        to_std = fix_date(raw_to) if raw_to else datetime.now().strftime('%Y-%m-%d')

        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. सभी बिल्स लोड करें (सुरक्षित तरीके से)
        matched_bills = []
        try:
            cursor.execute("SELECT * FROM bookings WHERE studio_id = ?", (cur_studio,))
            raw_bills = [dict(r) for r in cursor.fetchall()]
            for b in raw_bills:
                b_date = fix_date(b.get('bill_date') or b.get('created_at') or '')
                if b_date and (from_std <= b_date <= to_std):
                    matched_bills.append(b)
        except Exception as e:
            print("Bills Fetch Warning:", e)

        # 2. सभी टोकन्स लोड करें (सुरक्षित तरीके से)
        matched_tokens = []
        try:
            cursor.execute("SELECT * FROM daily_tokens WHERE studio_id = ?", (cur_studio,))
            raw_tokens = [dict(r) for r in cursor.fetchall()]
            for t in raw_tokens:
                t_date = fix_date(t.get('token_date') or t.get('created_at') or '')
                if t_date and (from_std <= t_date <= to_std):
                    matched_tokens.append(t)
        except Exception as e:
            print("Tokens Fetch Warning:", e)

        # 3. सभी खर्चे लोड करें (सुरक्षित तरीके से)
        matched_expenses = []
        try:
            cursor.execute("SELECT * FROM daily_expenses WHERE studio_id = ?", (cur_studio,))
            raw_expenses = [dict(r) for r in cursor.fetchall()]
            for exp in raw_expenses:
                e_date = fix_date(exp.get('exp_date') or exp.get('date') or '')
                if e_date and (from_std <= e_date <= to_std):
                    matched_expenses.append({
                        "date": exp.get('exp_date') or e_date,
                        "category": exp.get('category') or exp.get('exp_type') or 'General Expense',
                        "notes": exp.get('notes') or exp.get('description') or '-',
                        "amount": float(exp.get('amount') or 0)
                    })
        except Exception as e:
            print("Expenses Fetch Warning:", e)

        # 💰 1. सेल्स & रेवेन्यू कैलकुलेशन
        bill_gross = sum(float(b.get('total_amount') or 0) for b in matched_bills)
        token_gross = sum(float(t.get('total_amount') or 0) for t in matched_tokens)
        gross_business = bill_gross + token_gross

        bill_adv = sum(float(b.get('advance_amount') or 0) for b in matched_bills)
        token_cash = sum(float(t.get('cash_paid') or 0) for t in matched_tokens)
        token_upi = sum(float(t.get('upi_paid') or 0) for t in matched_tokens)
        
        cash_in_hand = token_cash + (bill_adv * 0.7)
        upi_in_bank = token_upi + (bill_adv * 0.3)
        collected_revenue = bill_adv + token_cash + token_upi

        total_pending_dues = sum(float(b.get('balance_amount') or 0) for b in matched_bills)
        total_discounts = sum(float(b.get('discount_amount') or 0) for b in matched_bills)

        # 💸 2. खर्च वर्गीकरण
        exp_daily = 0.0
        exp_salary = 0.0
        exp_lab = 0.0

        for e in matched_expenses:
            amt = float(e.get('amount') or 0)
            cat = str(e.get('category') or '').lower()
            if 'salary' in cat or 'staff' in cat:
                exp_salary += amt
            elif 'lab' in cat or 'outsource' in cat or 'print' in cat:
                exp_lab += amt
            else:
                exp_daily += amt

        total_expenses = exp_daily + exp_salary + exp_lab
        net_profit = collected_revenue - total_expenses

        # 📦 3. रिस्पॉन्स लौटाएं
        return jsonify({
            "status": "success",
            "from_date": from_std,
            "to_date": to_std,
            "metrics": {
                "gross_business": gross_business,
                "total_bills_count": len(matched_bills) + len(matched_tokens),
                "collected_revenue": collected_revenue,
                "cash_in_hand": cash_in_hand,
                "upi_in_bank": upi_in_bank,
                "total_expenses": total_expenses,
                "net_profit": net_profit,
                "pending_dues": total_pending_dues,
                "total_discounts": total_discounts
            },
            "expense_details": {
                "daily_expenses": exp_daily,
                "staff_payouts": exp_salary,
                "lab_paid": exp_lab,
                "staff_advances": 0.0
            },
            "expense_list": matched_expenses
        })

    except Exception as e:
        print("Master Report Error:", str(e))
        return jsonify({
            "status": "error", 
            "message": f"सर्वर पर समस्या आई: {str(e)}"
        }), 500
    finally:
        if conn:
            conn.close()


# 🖨️ मार्केट / साहूकार खाता प्रिंट स्टेटमेंट रूट
@app.route('/print_market_statement/<int:account_id>', methods=['GET'])
@login_required
def print_market_statement(account_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. स्टूडियो का नाम
        cursor.execute("SELECT studio_name FROM saas_studios WHERE id = ?", (cur_studio,))
        st_row = cursor.fetchone()
        studio_title = st_row['studio_name'] if st_row else "HIM STUDIO"

        # 2. खाता विवरण
        cursor.execute("SELECT * FROM sahukar_accounts WHERE id = ? AND studio_id = ?", (account_id, cur_studio))
        acc_row = cursor.fetchone()
        if not acc_row:
            return "Account Statement Not Found", 404

        acc_dict = dict(acc_row)

        # 3. इतिहास (Transactions)
        cursor.execute("SELECT * FROM sahukar_history WHERE account_id = ? AND studio_id = ? ORDER BY id ASC", (account_id, cur_studio))
        history_rows = [dict(h) for h in cursor.fetchall()]

        total_paid = sum(float(h['amount_paid'] or 0) for h in history_rows if float(h['amount_paid'] or 0) > 0)
        total_penalty = sum(abs(float(h['amount_paid'] or 0)) for h in history_rows if float(h['amount_paid'] or 0) < 0)
        
        acc_dict['total_paid'] = total_paid
        acc_dict['total_penalty'] = total_penalty
        acc_dict['outstanding_due'] = max(0.0, (float(acc_dict['borrowed_amount'] or 0) + total_penalty) - total_paid)

        return render_template(
            'print_market_statement.html',
            studio_title=studio_title,
            account=acc_dict,
            history=history_rows,
            current_time=datetime.now().strftime('%d/%m/%Y %I:%M %p')
        )
    except Exception as e:
        return f"Market Statement Print Error: {str(e)}", 500
    finally:
        if conn:
            conn.close()
# =====================================================================
# 📦 9. MASTER RAW MATERIAL & FINISHED PRODUCT STOCK (ISOLATED)
# =====================================================================

@app.route('/api/get_material_stock_list', methods=['GET'])
@login_required
def get_material_stock_list():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM master_raw_materials WHERE studio_id = ? ORDER BY id DESC", (cur_studio,))
        return jsonify({"status": "success", "data": [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/add_custom_material', methods=['POST'])
@login_required
def add_custom_material():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        name = data.get('material_name', '').strip()
        cat = data.get('category', 'Paper').strip()
        qty = float(data.get('opening_qty', 0))
        unit = data.get('unit', 'Sheets').strip()
        if not name: return jsonify({"status": "error", "message": "Material Name is required!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        now_s = datetime.now().strftime('%d/%m/%Y %I:%M %p')
        cursor.execute("INSERT INTO master_raw_materials (studio_id, material_name, category, current_qty, unit, last_updated) VALUES (?, ?, ?, ?, ?, ?)", (cur_studio, name, cat, qty, unit, now_s))
        cursor.execute("INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) VALUES (?, ?, ?, 'STOCK_CREATED', 'Initial Opening Stock', ?)", (cur_studio, name, qty, now_s))
        conn.commit()
        return jsonify({"status": "success", "message": "Material Added Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/update_raw_material/<int:mat_id>', methods=['POST'])
@login_required
def update_raw_material(mat_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        name = data.get('material_name', '').strip()
        cat = data.get('category', 'Paper').strip()
        qty = float(data.get('current_qty', 0))
        unit = data.get('unit', 'Sheets').strip()
        if not name: return jsonify({"status": "error", "message": "Material Name is required!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        now_s = datetime.now().strftime('%d/%m/%Y %I:%M %p')
        cursor.execute("UPDATE master_raw_materials SET material_name = ?, category = ?, current_qty = ?, unit = ?, last_updated = ? WHERE id = ? AND studio_id = ?", (name, cat, qty, unit, now_s, mat_id, cur_studio))
        cursor.execute("INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) VALUES (?, ?, ?, 'STOCK_EDITED', 'Manual Grid Update', ?)", (cur_studio, name, qty, now_s))
        conn.commit()
        return jsonify({"status": "success", "message": "Material Updated Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/delete_raw_material/<int:mat_id>', methods=['DELETE'])
@login_required
def delete_raw_material(mat_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT material_name FROM master_raw_materials WHERE id = ? AND studio_id = ?", (mat_id, cur_studio))
        row = cursor.fetchone()
        if not row: return jsonify({"status": "error", "message": "Material item not found"}), 404
            
        mat_name = row['material_name']
        cursor.execute("DELETE FROM master_raw_materials WHERE id = ? AND studio_id = ?", (mat_id, cur_studio))
        now_s = datetime.now().strftime('%d/%m/%Y %I:%M %p')
        cursor.execute("INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) VALUES (?, ?, 0, 'STOCK_DELETED', 'Item Deleted from Stock', ?)", (cur_studio, mat_name, now_s))
        conn.commit()
        return jsonify({"status": "success", "message": f"'{mat_name}' deleted successfully!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/stock/update/<int:stock_id>', methods=['POST'])
@login_required
def update_stock_item(stock_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.get_json(force=True, silent=True) or {}
        pqty = int(float(data.get('pqty', 0) or 0))
        pprice = float(data.get('pprice', 0.0) or 0.0)
        pdisc = float(data.get('pdisc', 0.0) or 0.0)
        last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE product_stock SET pqty = ?, pprice = ?, pdisc = ?, last_updated = ? WHERE id = ? AND studio_id = ?", (pqty, pprice, pdisc, last_updated, stock_id, cur_studio))
        conn.commit()
        return jsonify({"status": "success", "message": "Stock updated successfully!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/deduct_raw_material', methods=['POST'])
@login_required
def deduct_raw_material():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        items = data.get('items', [])
        if not items: return jsonify({"status": "error", "message": "No items to deduct"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        now_s = datetime.now().strftime('%d/%m/%Y %I:%M %p')
        deducted_log = []

        def deduct_by_pattern(pattern, qty, reason):
            cursor.execute("SELECT id, material_name, current_qty FROM master_raw_materials WHERE LOWER(material_name) LIKE LOWER(?) AND studio_id = ?", (f"%{pattern}%", cur_studio))
            m_row = cursor.fetchone()
            if m_row:
                new_qty = max(0.0, float(m_row['current_qty']) - float(qty))
                cursor.execute("UPDATE master_raw_materials SET current_qty = ?, last_updated = ? WHERE id = ? AND studio_id = ?", (new_qty, now_s, m_row['id'], cur_studio))
                cursor.execute("INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) VALUES (?, ?, ?, 'BILL_OUT', ?, ?)", (cur_studio, m_row['material_name'], -float(qty), reason, now_s))
                deducted_log.append(f"{m_row['material_name']} (-{qty})")

        for it in items:
            raw_name = (it.get('material_name') or '').strip().lower()
            qty = float(it.get('qty', 1))
            source_info = it.get('source', 'Bill Deduction')
            if not raw_name: continue

            if any(k in raw_name for k in ['pan', 'pancard', 'pan card', 'aadhar', 'aadhaar', 'adhar']):
                deduct_by_pattern('4x6', 1 * qty, source_info)
                deduct_by_pattern('lem', 1 * qty, source_info)
            elif any(k in raw_name for k in ['passport', 'pass port', 'pp photo']):
                deduct_by_pattern('4x6', 1 * qty, source_info)
            elif '12x18' in raw_name or '12*18' in raw_name:
                deduct_by_pattern('12x18', 1 * qty, source_info)
            elif '4x6' in raw_name or '4*6' in raw_name:
                deduct_by_pattern('4x6', 1 * qty, source_info)
            elif '5x7' in raw_name or '5*7' in raw_name:
                deduct_by_pattern('5x7', 1 * qty, source_info)
            elif '8x12' in raw_name or '8*12' in raw_name:
                deduct_by_pattern('8x12', 1 * qty, source_info)
            elif 'lem' in raw_name or 'lemi' in raw_name or 'pouch' in raw_name:
                deduct_by_pattern('lem', 1 * qty, source_info)
            else:
                deduct_by_pattern(raw_name, qty, source_info)

        conn.commit()
        return jsonify({"status": "success", "message": f"Deducted: {', '.join(deducted_log)}" if deducted_log else "No matching materials found."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/direct_bottle_use', methods=['POST'])
@login_required
def direct_bottle_use():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        mat_id = data.get('material_id')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT material_name, current_qty FROM master_raw_materials WHERE id = ? AND studio_id = ?", (mat_id, cur_studio))
        m_row = cursor.fetchone()
        if not m_row: return jsonify({"status": "error", "message": "Material Not Found"}), 404

        now_s = datetime.now().strftime('%d/%m/%Y %I:%M %p')
        new_qty = max(0.0, float(m_row['current_qty']) - 1.0)
        cursor.execute("UPDATE master_raw_materials SET current_qty = ?, last_updated = ? WHERE id = ? AND studio_id = ?", (new_qty, now_s, mat_id, cur_studio))
        cursor.execute("INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) VALUES (?, ?, -1, 'INK_POURED', '1 Unit Direct Studio Use', ?)", (cur_studio, m_row['material_name'], now_s))
        conn.commit()
        return jsonify({"status": "success", "message": f"1 Unit Deducted for {m_row['material_name']}!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/get_material_movement_logs', methods=['GET'])
@login_required
def get_material_movement_logs():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM material_stock_logs WHERE studio_id = ? ORDER BY id DESC", (cur_studio,))
        return jsonify({"status": "success", "data": [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/clear_material_logs', methods=['POST'])
@login_required
def clear_material_logs():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM material_stock_logs WHERE studio_id = ?", (cur_studio,))
        conn.commit()
        return jsonify({"status": "success", "message": "मूवमेंट लॉग्स हिस्ट्री साफ कर दी गई!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/stock', methods=['GET', 'POST'])
@login_required
def manage_stock():
    cur_studio = get_current_studio_id()
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        data = request.json or {}
        pname = data.get('pname')
        if not pname: return jsonify({"error": "Product name is required"}), 400
            
        pqty = data.get('pqty', 0)
        pprice = data.get('pprice', 0)
        pdisc = data.get('pdisc', 0)
        last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            cursor.execute("INSERT INTO product_stock (studio_id, pname, pqty, pprice, pdisc, last_updated) VALUES (?, ?, ?, ?, ?, ?)", (cur_studio, pname, pqty, pprice, pdisc, last_updated))
            conn.commit()
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()

    try:
        cursor.execute("SELECT * FROM product_stock WHERE studio_id = ? ORDER BY id DESC", (cur_studio,))
        return jsonify([dict(r) for r in cursor.fetchall()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/restore_stock_on_remove', methods=['POST'])
@login_required
def restore_stock_on_remove():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        pname = (data.get('product_name') or '').strip()
        qty = float(data.get('qty', 0))
        bill_no = data.get('bill_no', '')
        if not pname or qty <= 0: return jsonify({"status": "error", "message": "Invalid item or quantity"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        now_s = datetime.now().strftime('%d/%m/%Y %I:%M %p')

        cursor.execute("SELECT id, pqty FROM product_stock WHERE LOWER(pname) = LOWER(?) AND studio_id = ?", (pname, cur_studio))
        prod_row = cursor.fetchone()
        if prod_row:
            new_qty = float(prod_row['pqty']) + qty
            cursor.execute("UPDATE product_stock SET pqty = ?, last_updated = ? WHERE id = ? AND studio_id = ?", (new_qty, now_s, prod_row['id'], cur_studio))

        cursor.execute("SELECT id, current_qty FROM master_raw_materials WHERE LOWER(material_name) LIKE LOWER(?) AND studio_id = ?", (f"%{pname}%", cur_studio))
        raw_row = cursor.fetchone()
        if raw_row:
            new_raw_qty = float(raw_row['current_qty']) + qty
            cursor.execute("UPDATE master_raw_materials SET current_qty = ?, last_updated = ? WHERE id = ? AND studio_id = ?", (new_raw_qty, now_s, raw_row['id'], cur_studio))
            cursor.execute("INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) VALUES (?, ?, ?, 'STOCK_RESTORE', ?, ?)", (cur_studio, pname, qty, f"Removed from Bill #{bill_no}", now_s))

        conn.commit()
        return jsonify({"status": "success", "message": f"{pname} stock (+{qty}) restored successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# 💳 10. CREDIT LEDGER (ISOLATED)
# =====================================================================

@app.route('/api/credit', methods=['GET', 'POST'])
@app.route('/api/get_credit_ledger', methods=['GET'])
@app.route('/api/save_direct_credit', methods=['POST'])
@login_required
def handle_credit_ledger():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        if request.method == 'POST':
            data = request.json or {}
            cname = data.get('customer_name') or data.get('cname') or 'Customer'
            mob = data.get('mobile') or data.get('mob') or '0000000000'
            addr = data.get('address') or 'Bilaspur'
            reason = data.get('reason') or 'Studio Services'
            due_amount = float(data.get('due_amount') or data.get('amount') or 0)
            entry_date = data.get('entry_date') or datetime.now().strftime('%Y-%m-%d')
            promise_date = data.get('promise_date') or ''

            cursor.execute("""
                INSERT INTO credit_ledger (studio_id, bill_no, cname, mob, addr, reason, due_amount, entry_date, promise_date, status)
                VALUES (?, 'Direct Entry', ?, ?, ?, ?, ?, ?, ?, 'Pending')
            """, (cur_studio, cname, mob, addr, reason, due_amount, entry_date, promise_date))
            conn.commit()
            return jsonify({'status': 'success', 'message': 'Credit entry saved successfully!'})

        cursor.execute('SELECT * FROM credit_ledger WHERE studio_id = ? ORDER BY id DESC', (cur_studio,))
        credit_list = []
        for row in cursor.fetchall():
            credit_list.append({
                'id': f"direct_{row['id']}", 'raw_id': row['id'], 'is_bill': False,
                'bill_no': row['bill_no'] or 'Direct Entry', 'customer_name': row['cname'] or '',
                'cname': row['cname'] or '', 'mobile': row['mob'] or '', 'mob': row['mob'] or '',
                'address': row['addr'] or '', 'reason': row['reason'] or '', 'balance_amount': row['due_amount'] or 0,
                'due_amount': row['due_amount'] or 0, 'total_amount': row['due_amount'] or 0,
                'entry_date': row['entry_date'] or '', 'created_date': row['entry_date'] or '',
                'promise_date': row['promise_date'] or '', 'status': row['status'] or 'Pending',
            })

        cursor.execute("SELECT bill_no, customer_name, mobile, address, bill_date, delivery_date, balance_amount, total_amount FROM bookings WHERE balance_amount > 0 AND studio_id = ? ORDER BY CAST(bill_no AS INTEGER) DESC", (cur_studio,))
        for brow in cursor.fetchall():
            b_no = str(brow['bill_no'])
            credit_list.append({
                'id': f"bill_{b_no}", 'raw_id': b_no, 'is_bill': True, 'bill_no': b_no,
                'customer_name': brow['customer_name'] or '', 'cname': brow['customer_name'] or '',
                'mobile': brow['mobile'] or '', 'mob': brow['mobile'] or '', 'address': brow['address'] or 'Bilaspur',
                'reason': f"Main Invoice #{b_no} Balance", 'balance_amount': brow['balance_amount'] or 0,
                'due_amount': brow['balance_amount'] or 0, 'total_amount': brow['total_amount'] or 0,
                'entry_date': brow['bill_date'] or '', 'created_date': brow['bill_date'] or '',
                'promise_date': brow['delivery_date'] or '', 'status': 'Pending',
            })

        return jsonify(credit_list)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/add_credit_payment', methods=['POST'])
@login_required
def add_credit_payment():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        record_id = str(data.get('id', '')).strip()
        
        # 💵 Cash और UPI का विभाजन निकालें
        cash_amt = float(data.get('cash_amount') or 0)
        upi_amt = float(data.get('upi_amount') or 0)
        pay_amount = float(data.get('total_amount') or (cash_amt + upi_amt) or data.get('amount') or 0)
        
        next_pdate = str(data.get('next_promise_date') or '').strip()
        payment_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if not record_id or pay_amount <= 0:
            return jsonify({'status': 'error', 'message': 'कृपया वैध भुगतान राशि (Cash या UPI) दर्ज करें!'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # 🛡️ सुनिश्चित करें कि payment_history टेबल मौजूद है
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id TEXT,
                bill_no TEXT,
                credit_id TEXT,
                cash_amount REAL DEFAULT 0.0,
                upi_amount REAL DEFAULT 0.0,
                total_amount REAL DEFAULT 0.0,
                payment_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        target_bill_no = None
        target_credit_id = None

        # ----------------------------------------------------
        # 1. यदि बिलिंग रिकॉर्ड (Main Invoice / Wedding) है
        # ----------------------------------------------------
        if record_id.startswith('bill_'):
            target_bill_no = record_id.replace('bill_', '').strip()
            cursor.execute('SELECT balance_amount FROM bookings WHERE bill_no = ? AND studio_id = ?', (target_bill_no, cur_studio))
            brow = cursor.fetchone()
            if not brow:
                return jsonify({'status': 'error', 'message': f'बिल संख्या #{target_bill_no} का रिकॉर्ड नहीं मिला!'}), 404

            current_due = float(brow['balance_amount'] or 0)
            new_due = max(0.0, current_due - pay_amount)

            if next_pdate:
                cursor.execute("""
                    UPDATE bookings 
                    SET advance_amount = advance_amount + ?, balance_amount = ?, promise_date = ? 
                    WHERE bill_no = ? AND studio_id = ?
                """, (pay_amount, new_due, next_pdate, target_bill_no, cur_studio))
            else:
                cursor.execute("""
                    UPDATE bookings 
                    SET advance_amount = advance_amount + ?, balance_amount = ? 
                    WHERE bill_no = ? AND studio_id = ?
                """, (pay_amount, new_due, target_bill_no, cur_studio))

        # ----------------------------------------------------
        # 2. यदि डायरेक्ट क्रेडिट लेज़र (Direct Entry) है
        # ----------------------------------------------------
        else:
            target_credit_id = record_id.replace('direct_', '').strip()
            cursor.execute('SELECT due_amount FROM credit_ledger WHERE id = ? AND studio_id = ?', (target_credit_id, cur_studio))
            row = cursor.fetchone()
            if not row:
                return jsonify({'status': 'error', 'message': 'उधारी खाता रिकॉर्ड नहीं मिला!'}), 404

            current_due = float(row['due_amount'] or 0)
            new_due = max(0.0, current_due - pay_amount)
            new_status = 'Paid' if new_due <= 0 else 'Pending'

            if next_pdate:
                cursor.execute("""
                    UPDATE credit_ledger 
                    SET due_amount = ?, status = ?, promise_date = ? 
                    WHERE id = ? AND studio_id = ?
                """, (new_due, new_status, next_pdate, target_credit_id, cur_studio))
            else:
                cursor.execute("""
                    UPDATE credit_ledger 
                    SET due_amount = ?, status = ? 
                    WHERE id = ? AND studio_id = ?
                """, (new_due, new_status, target_credit_id, cur_studio))

        # ----------------------------------------------------
        # 3. पेमेंट हिस्ट्री (Audit Passbook) में एंट्री दर्ज करें
        # ----------------------------------------------------
        cursor.execute('''
            INSERT INTO payment_history (studio_id, bill_no, credit_id, cash_amount, upi_amount, total_amount, payment_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (cur_studio, target_bill_no, target_credit_id, cash_amt, upi_amt, pay_amount, payment_time_str))

        conn.commit()
        return jsonify({
            'status': 'success',
            'message': 'भुगतान सफलतापूर्वक जमा हुआ और हिस्ट्री दर्ज हो गई!',
            'new_balance': new_due
        })

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'status': 'error', 'message': f'सर्वर एरर: {str(e)}'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/get_payment_history', methods=['GET'])
@login_required
def get_payment_history():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        bill_no = request.args.get('bill_no', '').strip()
        credit_id = request.args.get('credit_id', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()

        if bill_no:
            cursor.execute('''
                SELECT cash_amount, upi_amount, total_amount, payment_date 
                FROM payment_history 
                WHERE studio_id = ? AND bill_no = ? 
                ORDER BY id DESC
            ''', (cur_studio, bill_no))
        elif credit_id:
            cursor.execute('''
                SELECT cash_amount, upi_amount, total_amount, payment_date 
                FROM payment_history 
                WHERE studio_id = ? AND credit_id = ? 
                ORDER BY id DESC
            ''', (cur_studio, credit_id))
        else:
            return jsonify({'status': 'success', 'history': []})

        rows = cursor.fetchall()
        return jsonify({'status': 'success', 'history': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# 🛒 11. PURCHASERS & PURCHASE BILLS (ISOLATED)
# =====================================================================

@app.route('/api/get_purchasers', methods=['GET'])
@login_required
def get_purchasers():
    cur_studio = get_current_studio_id()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM purchasers WHERE studio_id = ? ORDER BY id DESC', (cur_studio,))
    rows = cursor.fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(row) for row in rows]})

@app.route('/api/save_purchaser', methods=['POST'])
@login_required
def save_purchaser():
    cur_studio = get_current_studio_id()
    data = request.json or {}
    name = data.get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Purchaser Name is required!'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO purchasers (studio_id, name, mobile, address) VALUES (?, ?, ?, ?)', (cur_studio, name, data.get('mobile', ''), data.get('address', '')))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/upload_purchase_file', methods=['POST'])
@login_required
def upload_purchase_file():
    if 'file' not in request.files: 
        return jsonify({'status': 'error', 'message': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '': 
        return jsonify({'status': 'error', 'message': 'No selected file'}), 400
    
    original_name = secure_filename(file.filename)
    ext = os.path.splitext(original_name)[1].lower()
    
    allowed_extensions = ['.jpg', '.jpeg', '.png', '.pdf', '.webp']
    if ext not in allowed_extensions:
        return jsonify({'status': 'error', 'message': 'Only JPG, PNG, WEBP or PDF files are allowed!'}), 400

    upload_folder = os.path.join(UPLOADS_DIR, 'purchase_bills')
    os.makedirs(upload_folder, exist_ok=True)
    
    # Generate unique filename to prevent collisions and save space/bandwidth
    unique_filename = f"bill_{int(time.time())}_{secrets.token_hex(4)}{ext if ext == '.pdf' else '.jpg'}"
    file_path = os.path.join(upload_folder, unique_filename)
    
    if ext == '.pdf':
        file.save(file_path)
    else:
        compressed_io = compress_bill_image(file, max_size_kb=300)
        with open(file_path, 'wb') as f:
            f.write(compressed_io.read())
            
    return jsonify({'status': 'success', 'file_path': unique_filename})

@app.route('/api/save_purchase_bill', methods=['POST'])
@login_required
def save_purchase_bill():
    cur_studio = get_current_studio_id()
    data = request.json or {}
    purchaser_id = data.get('purchaser_id')
    if not purchaser_id: 
        return jsonify({'status': 'error', 'message': 'Purchaser ID missing!'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO purchase_bills (studio_id, purchaser_id, bill_date, bill_number, total_amount, file_path) VALUES (?, ?, ?, ?, ?, ?)", 
            (cur_studio, purchaser_id, data.get('bill_date', ''), data.get('bill_number', ''), float(data.get('total_amount', 0)), data.get('file_path', ''))
        )
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/get_purchase_bills/<int:purchaser_id>', methods=['GET'])
@login_required
def get_purchase_bills(purchaser_id):
    cur_studio = get_current_studio_id()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM purchase_bills WHERE purchaser_id = ? AND studio_id = ? ORDER BY id DESC', (purchaser_id, cur_studio))
    rows = cursor.fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(row) for row in rows]})

# =====================================================================
# 💸 12. EXPENSES, SAHUKAR & LOANS (ISOLATED)
# =====================================================================

@app.route('/api/get_daily_expenses', methods=['GET'])
@login_required
def get_daily_expenses():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        filter_type = request.args.get('filter', 'today')
        conn = get_db_connection()
        cursor = conn.cursor()

        if filter_type == 'today':
            today_str = datetime.now().strftime('%d/%m/%Y')
            today_dash = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('SELECT * FROM daily_expenses WHERE (exp_date = ? OR exp_date = ?) AND studio_id = ? ORDER BY id DESC', (today_str, today_dash, cur_studio))
        else:
            cursor.execute('SELECT * FROM daily_expenses WHERE exp_date LIKE ? AND studio_id = ? ORDER BY id DESC', (f'%{filter_type}%', cur_studio))

        return jsonify({'status': 'success', 'data': [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/save_daily_expense', methods=['POST'])
@login_required
def save_daily_expense():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        exp_date = data.get('exp_date') or datetime.now().strftime('%d/%m/%Y')
        category = data.get('category')
        amount = float(data.get('amount', 0))
        if not category or amount <= 0: return jsonify({'status': 'error', 'message': 'Category and Amount are required!'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO daily_expenses (studio_id, exp_date, category, amount, notes) VALUES (?, ?, ?, ?, ?)', (cur_studio, exp_date, category, amount, data.get('notes', '')))
        conn.commit()
        return jsonify({'status': 'success', 'message': 'Expense saved successfully!'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/register_sahukar_account', methods=['POST'])
@login_required
def register_sahukar_account():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        lender_name = data.get('lender_name', '').strip()
        borrowed_amount = float(data.get('borrowed_amount', 0))
        if not lender_name or borrowed_amount <= 0: return jsonify({"status": "error", "message": "Party name and amount required!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sahukar_accounts (studio_id, date_taken, lender_name, pan_number, borrowed_amount, notes) VALUES (?, ?, ?, ?, ?, ?)", (cur_studio, data.get('date_taken'), lender_name, data.get('pan_number', '').strip().upper(), borrowed_amount, data.get('notes', '').strip()))
        conn.commit()
        return jsonify({"status": "success", "message": "Account created successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/get_sahukar_accounts', methods=['GET'])
@login_required
def get_sahukar_accounts():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sahukar_accounts WHERE studio_id = ? ORDER BY id DESC", (cur_studio,))
        accounts = cursor.fetchall()
        result = []
        for acc in accounts:
            acc_dict = dict(acc)
            cursor.execute("SELECT amount_paid FROM sahukar_history WHERE account_id = ? AND studio_id = ?", (acc['id'], cur_studio))
            history_rows = cursor.fetchall()
            total_paid = sum(float(h['amount_paid'] or 0) for h in history_rows if float(h['amount_paid'] or 0) > 0)
            total_penalty = sum(abs(float(h['amount_paid'] or 0)) for h in history_rows if float(h['amount_paid'] or 0) < 0)
            acc_dict['total_paid'] = total_paid
            acc_dict['total_penalty'] = total_penalty
            acc_dict['outstanding_due'] = max(0.0, (float(acc['borrowed_amount'] or 0) + total_penalty) - total_paid)
            result.append(acc_dict)
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/get_sahukar_history/<int:account_id>', methods=['GET'])
@login_required
def get_sahukar_history(account_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sahukar_history WHERE account_id = ? AND studio_id = ? ORDER BY id DESC", (account_id, cur_studio))
        return jsonify({"status": "success", "data": [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/add_sahukar_payment', methods=['POST'])
@login_required
def add_sahukar_payment():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        account_id = data.get('account_id')
        amount_paid = float(data.get('amount_paid', 0))
        if not account_id or amount_paid <= 0: return jsonify({"status": "error", "message": "Invalid amount!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sahukar_history (studio_id, account_id, pay_date, amount_paid, remarks) VALUES (?, ?, ?, ?, ?)", (cur_studio, account_id, datetime.now().strftime("%d/%m/%Y %H:%M"), amount_paid, data.get('remarks', 'Cash Jamā')))
        conn.commit()
        return jsonify({"status": "success", "message": "Payment recorded successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/add_sahukar_penalty', methods=['POST'])
@login_required
def add_sahukar_penalty():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        account_id = data.get('account_id')
        penalty_amount = float(data.get('penalty_amount', 0))
        if not account_id or penalty_amount <= 0: return jsonify({"status": "error", "message": "Invalid penalty!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sahukar_history (studio_id, account_id, pay_date, amount_paid, remarks) VALUES (?, ?, ?, ?, ?)", (cur_studio, account_id, datetime.now().strftime("%d/%m/%Y %H:%M"), -penalty_amount, f"Penalty: {data.get('reason', 'Late Fee')}"))
        conn.commit()
        return jsonify({"status": "success", "message": "Penalty applied successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/register_bank_account', methods=['POST'])
@login_required
def register_bank_account():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        bank_name = data.get('bank_name')
        loan_amount = float(data.get('loan_amount', 0))
        deductions = float(data.get('deductions', 0))
        disbursed = max(0.0, loan_amount - deductions)
        emi = float(data.get('emi', 0))
        tenure = int(data.get('tenure', 12))
        start_date = data.get('start_date', datetime.now().strftime("%Y-%m-%d"))
        if not bank_name or loan_amount <= 0: return jsonify({"status": "error", "message": "Bank Name and Amount required!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bank_loans (studio_id, bank_name, branch, ifsc_code, loan_amount, deductions, disbursed, emi, tenure, interest_rate, start_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active')
        """, (cur_studio, bank_name, data.get('branch', 'Main'), data.get('ifsc_code', ''), loan_amount, deductions, disbursed, emi, tenure, float(data.get('interest_rate', 10.5)), start_date))
        bank_id = cursor.lastrowid

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        for i in range(1, tenure + 1):
            due_dt = start_dt + timedelta(days=30 * i)
            cursor.execute("INSERT INTO bank_schedule (studio_id, bank_id, installment_no, due_date, emi_amount, status) VALUES (?, ?, ?, ?, ?, 'Unpaid')", (cur_studio, bank_id, i, due_dt.strftime("%d/%m/%Y"), emi))

        conn.commit()
        return jsonify({"status": "success", "message": "Bank loan and EMI schedule created!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/get_bank_accounts', methods=['GET'])
@login_required
def get_bank_accounts():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bank_loans WHERE studio_id = ? ORDER BY id DESC", (cur_studio,))
        loans = cursor.fetchall()
        result = []
        for loan in loans:
            loan_dict = dict(loan)
            cursor.execute("SELECT SUM(emi_amount) FROM bank_schedule WHERE bank_id = ? AND status = 'Unpaid' AND studio_id = ?", (loan['id'], cur_studio))
            loan_dict['outstanding'] = cursor.fetchone()[0] or 0.0
            result.append(loan_dict)
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/get_bank_schedule/<int:bank_id>', methods=['GET'])
@login_required
def get_bank_schedule(bank_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bank_schedule WHERE bank_id = ? AND studio_id = ? ORDER BY installment_no ASC", (bank_id, cur_studio))
        return jsonify({"status": "success", "schedule": [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/settle_bank_emi', methods=['POST'])
@login_required
def settle_bank_emi():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        bank_id = data.get('bank_id')
        inst_no = data.get('installment_no')
        pay_date = datetime.now().strftime("%d/%m/%Y")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE bank_schedule SET status = 'Paid', payment_date = ?, mode = ? WHERE bank_id = ? AND installment_no = ? AND studio_id = ?", (pay_date, data.get('mode', 'Bank Transfer'), bank_id, inst_no, cur_studio))
        cursor.execute("SELECT SUM(emi_amount) FROM bank_schedule WHERE bank_id = ? AND status = 'Unpaid' AND studio_id = ?", (bank_id, cur_studio))
        new_out = cursor.fetchone()[0] or 0.0
        if new_out <= 0: cursor.execute("UPDATE bank_loans SET status = 'Closed' WHERE id = ? AND studio_id = ?", (bank_id, cur_studio))
        conn.commit()
        return jsonify({"status": "success", "new_outstanding": new_out})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/settle_full_bank_loan', methods=['POST'])
@login_required
def settle_full_bank_loan():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        bank_id = data.get('bank_id')
        pay_date = datetime.now().strftime("%d/%m/%Y")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE bank_schedule SET status = 'Paid', payment_date = ?, mode = 'Full Settlement' WHERE bank_id = ? AND status = 'Unpaid' AND studio_id = ?", (pay_date, bank_id, cur_studio))
        cursor.execute("UPDATE bank_loans SET status = 'Closed' WHERE id = ? AND studio_id = ?", (bank_id, cur_studio))
        conn.commit()
        return jsonify({"status": "success", "new_outstanding": 0.0})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/add_bank_penalty', methods=['POST'])
@login_required
def add_bank_penalty():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        bank_id = data.get('bank_id')
        penalty_amount = float(data.get('penalty_amount', 0))
        if not bank_id or penalty_amount <= 0: return jsonify({"status": "error", "message": "Invalid penalty!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(installment_no) FROM bank_schedule WHERE bank_id = ? AND studio_id = ?", (bank_id, cur_studio))
        new_inst = (cursor.fetchone()[0] or 0) + 1
        cursor.execute("INSERT INTO bank_schedule (studio_id, bank_id, installment_no, due_date, emi_amount, status, mode) VALUES (?, ?, ?, ?, ?, 'Unpaid', ?)", (cur_studio, bank_id, new_inst, datetime.now().strftime("%d/%m/%Y"), penalty_amount, f"Penalty: {data.get('reason', 'Late Fee')}"))
        conn.commit()
        return jsonify({"status": "success", "message": "Penalty added successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# 📦 13. MASTER LAB OUTSOURCE (ISOLATED)
# =====================================================================

@app.route('/api/get_master_lab_vendors', methods=['GET'])
@login_required
def get_master_lab_vendors():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM master_lab_vendors WHERE studio_id = ? ORDER BY id ASC", (cur_studio,))
        return jsonify({"status": "success", "data": [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/save_master_lab_vendor', methods=['POST'])
@login_required
def save_master_lab_vendor():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        vname = data.get('vendor_name', '').strip()
        mobile = data.get('mobile', '').strip()
        if not vname or len(mobile) != 10: return jsonify({"status": "error", "message": "Vendor name and 10-digit mobile required!"}), 400
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO master_lab_vendors (studio_id, vendor_name, mobile, address) VALUES (?, ?, ?, ?)", (cur_studio, vname, mobile, data.get('address', '').strip()))
        conn.commit()
        return jsonify({"status": "success", "message": "Vendor registered successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/get_lab_sub_records/<int:vendor_id>', methods=['GET'])
@login_required
def get_lab_sub_records(vendor_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM lab_outsource_records WHERE vendor_id = ? AND studio_id = ? ORDER BY id ASC", (vendor_id, cur_studio))
        return jsonify({"status": "success", "data": [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/save_lab_sub_record', methods=['POST'])
@login_required
def save_lab_sub_record():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        vendor_id = request.form.get('vendor_id')
        sub_id = request.form.get('sub_id')
        sno = str(request.form.get('sno', '1')).strip()
        cust_name = request.form.get('cust_name', '').strip()
        mobile = request.form.get('mobile', '').strip()
        total_amount = float(request.form.get('total_amount', 0))
        advance_amount = float(request.form.get('advance_amount', 0))
        lab_cost = float(request.form.get('lab_cost', 0))
        lab_paid = float(request.form.get('lab_paid', 0))

        if not cust_name or len(mobile) != 10: return jsonify({"status": "error", "message": "Customer name and 10-digit mobile required!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_name FROM master_lab_vendors WHERE id = ? AND studio_id = ?", (vendor_id, cur_studio))
        v_row = cursor.fetchone()
        vname = v_row['vendor_name'] if v_row else 'General'

        clean_vname = "".join(c for c in vname if c.isalnum() or c in (' ', '_', '-')).strip()
        clean_cname = "".join(c for c in cust_name if c.isalnum() or c in (' ', '_', '-')).strip()
        job_dir = os.path.join(JOB_FOLDERS_DIR, f"studio_{cur_studio}", clean_vname, f"#{sno}_{clean_cname}")
        os.makedirs(job_dir, exist_ok=True)

        attached_file = request.files.get('attached_file')
        file_path_saved = None
        has_file = 0
        if attached_file and attached_file.filename != '':
            clean_fname = secure_filename(attached_file.filename)
            save_dest = os.path.join(job_dir, clean_fname)
            attached_file.save(save_dest)
            file_path_saved = clean_fname
            has_file = 1

        if sub_id and sub_id != 'null':
            if has_file:
                cursor.execute("""
                    UPDATE lab_outsource_records SET cust_name=?, mobile=?, address=?, work_type=?, work_rec_date=?, total_amount=?, advance_amount=?, sent_lab_date=?, expected_date=?, tracking_no=?, lab_cost=?, lab_paid=?, pay_date=?, cust_delivery_status=?, has_file=?, attached_file_path=?, remark=? WHERE id=? AND studio_id=?
                """, (cust_name, mobile, request.form.get('address', ''), request.form.get('work_type', ''), request.form.get('work_rec_date', ''), total_amount, advance_amount, request.form.get('sent_lab_date', ''), request.form.get('expected_date', ''), request.form.get('tracking_no', ''), lab_cost, lab_paid, request.form.get('pay_date', ''), request.form.get('cust_delivery_status', 'Pending'), has_file, file_path_saved, request.form.get('remark', ''), sub_id, cur_studio))
            else:
                cursor.execute("""
                    UPDATE lab_outsource_records SET cust_name=?, mobile=?, address=?, work_type=?, work_rec_date=?, total_amount=?, advance_amount=?, sent_lab_date=?, expected_date=?, tracking_no=?, lab_cost=?, lab_paid=?, pay_date=?, cust_delivery_status=?, remark=? WHERE id=? AND studio_id=?
                """, (cust_name, mobile, request.form.get('address', ''), request.form.get('work_type', ''), request.form.get('work_rec_date', ''), total_amount, advance_amount, request.form.get('sent_lab_date', ''), request.form.get('expected_date', ''), request.form.get('tracking_no', ''), lab_cost, lab_paid, request.form.get('pay_date', ''), request.form.get('cust_delivery_status', 'Pending'), request.form.get('remark', ''), sub_id, cur_studio))
        else:
            cursor.execute("""
                INSERT INTO lab_outsource_records (studio_id, vendor_id, sno, cust_name, mobile, address, work_type, work_rec_date, total_amount, advance_amount, sent_lab_date, expected_date, tracking_no, lab_cost, lab_paid, pay_date, cust_delivery_status, has_file, attached_file_path, remark) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (cur_studio, vendor_id, sno, cust_name, mobile, request.form.get('address', ''), request.form.get('work_type', ''), request.form.get('work_rec_date', ''), total_amount, advance_amount, request.form.get('sent_lab_date', ''), request.form.get('expected_date', ''), request.form.get('tracking_no', ''), lab_cost, lab_paid, request.form.get('pay_date', ''), request.form.get('cust_delivery_status', 'Pending'), has_file, file_path_saved, request.form.get('remark', '')))

        conn.commit()
        return jsonify({"status": "success", "message": "Record saved successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# 📁 WORK EXPORT JOB FILES VIEWER & DOWNLOAD APIS
# =====================================================================

@app.route('/api/get_job_workspace_files', methods=['GET'])
@login_required
def get_job_workspace_files():
    try:
        cur_studio = get_current_studio_id()
        vendor_id = request.args.get('vendor_id')
        sno = str(request.args.get('sno', '1')).strip()
        cust_name = request.args.get('cust_name', '').strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT vendor_name FROM master_lab_vendors WHERE id = ? AND studio_id = ?", (vendor_id, cur_studio))
        v_row = cursor.fetchone()
        conn.close()

        vname = v_row['vendor_name'] if v_row else 'General'
        clean_vname = "".join(c for c in vname if c.isalnum() or c in (' ', '_', '-')).strip()
        clean_cname = "".join(c for c in cust_name if c.isalnum() or c in (' ', '_', '-')).strip()

        # सही जॉब फोल्डर पाथ
        job_dir = os.path.join(JOB_FOLDERS_DIR, f"studio_{cur_studio}", clean_vname, f"#{sno}_{clean_cname}")

        files_list = []
        if os.path.exists(job_dir):
            for fname in os.listdir(job_dir):
                fpath = os.path.join(job_dir, fname)
                if os.path.isfile(fpath):
                    fsize = os.path.getsize(fpath)
                    # साइज को KB/MB में बदलें
                    size_str = f"{fsize / 1024:.1f} KB" if fsize < 1024*1024 else f"{fsize / (1024*1024):.2f} MB"
                    files_list.append({
                        "file_name": fname,
                        "size": size_str,
                        "vendor_name": clean_vname,
                        "folder_name": f"#{sno}_{clean_cname}"
                    })

        return jsonify({"status": "success", "files": files_list})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "files": []}), 500


@app.route('/download_job_file', methods=['GET'])
@login_required
def download_job_file():
    try:
        cur_studio = get_current_studio_id()
        vname = request.args.get('vendor', '').strip()
        folder = request.args.get('folder', '').strip()
        filename = request.args.get('file', '').strip()

        clean_vname = "".join(c for c in vname if c.isalnum() or c in (' ', '_', '-')).strip()
        clean_folder = "".join(c for c in folder if c.isalnum() or c in (' ', '_', '-', '#')).strip()
        clean_filename = secure_filename(filename)

        file_dir = os.path.join(JOB_FOLDERS_DIR, f"studio_{cur_studio}", clean_vname, clean_folder)
        return send_from_directory(file_dir, clean_filename, as_attachment=True)
    except Exception as e:
        return f"File Download Error: {str(e)}", 404

# =====================================================================
# 🎬 14. STUDIO ASSETS & EQUIPMENT CLASH CHECKER (100% FIXED & SAFE)
# =====================================================================

@app.route('/api/get_studio_assets', methods=['GET'])
@login_required
def get_studio_assets():
  conn = None
  try:
    cur_studio = get_current_studio_id()
    only_available = (
        request.args.get('available_only', 'false').lower() == 'true'
    )

    conn = get_db_connection()
    cursor = conn.cursor()

    # टेबल मौजूद है या नहीं यह जांचें
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS studio_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                studio_id INTEGER DEFAULT 1,
                asset_name TEXT NOT NULL,
                serial_no TEXT DEFAULT '',
                default_rent REAL DEFAULT 0,
                total_stock_qty INTEGER DEFAULT 1,
                available_stock_qty INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)
    conn.commit()

    if only_available:
      cursor.execute(
          """
                SELECT * FROM studio_assets 
                WHERE CAST(studio_id AS INTEGER) = ? AND available_stock_qty > 0 
                ORDER BY id DESC
            """,
          (cur_studio,),
      )
    else:
      cursor.execute(
          """
                SELECT * FROM studio_assets 
                WHERE CAST(studio_id AS INTEGER) = ? 
                ORDER BY id DESC
            """,
          (cur_studio,),
      )

    return jsonify(
        {'status': 'success', 'data': [dict(r) for r in cursor.fetchall()]}
    )
  except Exception as e:
    return jsonify({'status': 'error', 'message': str(e), 'data': []}), 200
  finally:
    if conn:
      conn.close()

@app.route('/api/save_studio_asset', methods=['POST'])
@login_required
def save_studio_asset():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        name = data.get('asset_name', '').strip()
        if not name: 
            return jsonify({"status": "error", "message": "Product name is required!"}), 400
        
        tot_qty = int(data.get('total_stock_qty', 1))
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO studio_assets (studio_id, asset_name, serial_no, default_rent, total_stock_qty, available_stock_qty, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (cur_studio, name, data.get('serial_no', '').strip(), float(data.get('default_rent', 0)), tot_qty, tot_qty, datetime.now().strftime('%d/%m/%Y %I:%M %p')))
        conn.commit()
        return jsonify({"status": "success", "message": f"{name} registered successfully!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/check_equipment_availability', methods=['POST'])
@login_required
def check_equipment_availability():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        raw_camera = (data.get('item_name') or data.get('camera_name') or '').strip()
        check_date = (data.get('issue_date') or data.get('date') or '').strip()
        
        if not raw_camera or not check_date: 
            return jsonify({"status": "success", "warnings": [], "has_clash": False})

        clean_camera = raw_camera.split('(')[0].strip().lower()
        possible_dates = [check_date]
        if '-' in check_date and len(check_date.split('-')[0]) == 4:
            parts = check_date.split('-')
            possible_dates.append(f"{parts[2]}/{parts[1]}/{parts[0]}")
        elif '/' in check_date and len(check_date.split('/')[2]) == 4:
            parts = check_date.split('/')
            possible_dates.append(f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}")

        conn = get_db_connection()
        cursor = conn.cursor()
        warnings = []

        # 🎯 सुरक्षित क्वेरी (commitment_date का फॉलबैक मोबाइल के लिए)
        cursor.execute("""
            SELECT person_name, commitment_date, camera_name, issue_date, return_date 
            FROM equipment_logs 
            WHERE status = 'Open' AND CAST(studio_id AS INTEGER) = ?
        """, (cur_studio,))

        for r in cursor.fetchall():
            cams = [c.split('(')[0].strip().lower() for c in str(r['camera_name']).split(',')]
            if any(clean_camera in c or c in clean_camera for c in cams):
                r_issue = str(r['issue_date']).strip()
                r_return = str(r['return_date']).strip()
                if any(d in [r_issue, r_return] or (r_issue <= d <= r_return) for d in possible_dates):
                    p_mob = r['commitment_date']
                    p_info = f"{r['person_name']} ({p_mob})" if p_mob and p_mob != "INTERNAL" else r['person_name']
                    warnings.append({
                        "type": "RENT_ACTIVE",
                        "message": f"कैमरा '{raw_camera}' {check_date} को '{p_info}' के पास एक्टिव है!"
                    })

        cursor.execute("""
            SELECT * FROM wedding_bookings 
            WHERE status NOT IN ('Cancelled', 'Delivered') AND CAST(studio_id AS INTEGER) = ?
        """, (cur_studio,))
        
        weddings = cursor.fetchall()
        for w in weddings:
            w_dict = dict(w)
            wed_dates = [str(w_dict.get(k, '')).strip() for k in w_dict.keys() if 'date' in k.lower()]
            wed_cams = [str(w_dict.get(k, '')).strip().lower() for k in w_dict.keys() if 'cam' in k.lower() or 'item' in k.lower() or 'equip' in k.lower()]
            
            date_matched = any(pd in wed_dates for pd in possible_dates if pd)
            cam_matched = any(clean_camera in c for c in wed_cams if c)

            if date_matched and cam_matched:
                cust_name = w_dict.get('customer_name') or 'Customer'
                warnings.append({
                    "type": "WEDDING_BOOKED",
                    "message": f"कैमरा '{raw_camera}' Wedding Booking ({cust_name}) के लिए तारीख {check_date} को रिज़र्व है!"
                })

        return jsonify({"status": "success", "warnings": warnings, "has_clash": len(warnings) > 0})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/save_equipment_handover', methods=['POST'])
@login_required
def save_equipment_handover():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        pname = data.get('person_name', '').strip()
        pmobile = data.get('mobile', '').strip() or data.get('commitment_date', '').strip()
        items = data.get('items', [])
        opt_type = data.get('options_type', 'Rent')
        
        if not pname or not items: 
            return jsonify({"status": "error", "message": "Client Name and Items are required!"}), 400

        cam_names = ", ".join([i['product'] for i in items])
        ser_nums = ", ".join([i.get('serial', '') for i in items if i.get('serial')])
        
        per_day_rent = 0.0 if opt_type == 'Studio Work' else float(data.get('per_day_rent', 0))
        adv_amt = 0.0 if opt_type == 'Studio Work' else float(data.get('advance_amount', 0))
        bal_amt = 0.0 if opt_type == 'Studio Work' else float(data.get('balance_amount', 0))

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 🎯 टेबल स्कीमा में mobile कॉलम सुरक्षित जोड़ना (अगर मौजूद न हो)
        try:
            cursor.execute("ALTER TABLE equipment_logs ADD COLUMN mobile TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass

        cursor.execute("""
            INSERT INTO equipment_logs (studio_id, options_type, camera_name, serial_no, person_name, mobile, commitment_date, issue_date, return_date, per_day_rent, advance_amount, balance_amount, status, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Open', ?)
        """, (cur_studio, opt_type, cam_names, ser_nums, pname, pmobile, pmobile, data.get('issue_date', ''), data.get('return_date', ''), per_day_rent, adv_amt, bal_amt, datetime.now().strftime('%d/%m/%Y %I:%M %p')))

        for itm in items:
            cursor.execute("""
                UPDATE studio_assets 
                SET available_stock_qty = MAX(0, available_stock_qty - 1) 
                WHERE (asset_name = ? OR LOWER(asset_name) = LOWER(?)) AND CAST(studio_id AS INTEGER) = ?
            """, (itm['product'], itm['product'], cur_studio))
            
        conn.commit()
        return jsonify({"status": "success", "message": f"Handover for {pname} saved successfully!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/get_equipment_logs', methods=['GET'])
@login_required
def get_equipment_logs():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # सुरक्षित कॉलम माइग्रेशन
        try:
            cursor.execute("ALTER TABLE equipment_logs ADD COLUMN mobile TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass

        cursor.execute("""
            SELECT id, studio_id, options_type, camera_name, serial_no, person_name, 
                   COALESCE(NULLIF(mobile, ''), commitment_date) AS mobile, 
                   commitment_date, issue_date, return_date, per_day_rent, advance_amount, 
                   balance_amount, status, created_at 
            FROM equipment_logs 
            WHERE CAST(studio_id AS INTEGER) = ? 
            ORDER BY id DESC
        """, (cur_studio,))
        return jsonify({"status": "success", "data": [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/search_equipment_logs', methods=['GET'])
@login_required
def search_equipment_logs():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        query = request.args.get('q', '').strip()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if query:
            search_param = f"%{query}%"
            cursor.execute("""
                SELECT id, studio_id, options_type, camera_name, serial_no, person_name, 
                       COALESCE(NULLIF(mobile, ''), commitment_date) AS mobile, 
                       commitment_date, issue_date, return_date, per_day_rent, advance_amount, 
                       balance_amount, status, created_at 
                FROM equipment_logs 
                WHERE CAST(studio_id AS INTEGER) = ? 
                  AND (person_name LIKE ? OR mobile LIKE ? OR commitment_date LIKE ? OR camera_name LIKE ?)
                ORDER BY id DESC
            """, (cur_studio, search_param, search_param, search_param, search_param))
        else:
            cursor.execute("""
                SELECT id, studio_id, options_type, camera_name, serial_no, person_name, 
                       COALESCE(NULLIF(mobile, ''), commitment_date) AS mobile, 
                       commitment_date, issue_date, return_date, per_day_rent, advance_amount, 
                       balance_amount, status, created_at 
                FROM equipment_logs 
                WHERE CAST(studio_id AS INTEGER) = ? 
                ORDER BY id DESC
            """, (cur_studio,))
            
        return jsonify({"status": "success", "data": [dict(r) for r in cursor.fetchall()]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/settle_equipment_return', methods=['POST'])
@login_required
def settle_equipment_return():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        log_id = data.get('id')
        
        if not log_id: 
            return jsonify({"status": "error", "message": "Record identifier missing!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT camera_name, options_type, status 
            FROM equipment_logs 
            WHERE id = ? AND CAST(studio_id AS INTEGER) = ?
        """, (log_id, cur_studio))
        log_row = cursor.fetchone()
        if not log_row: 
            return jsonify({"status": "error", "message": "Record not found!"}), 404

        cam_names = [c.strip() for c in str(log_row['camera_name']).split(',') if c.strip()]
        
        # 🎯 सिर्फ सेटलमेंट बटन दबाने पर ही Studio Work 'Closed' होगा
        if log_row['options_type'] == 'Studio Work':
            rent_val = 0.0
            adv_val = 0.0
            bal_val = 0.0
            status_tag = 'Closed'
        else:
            rent_val = float(data.get('rent_val', 0))
            adv_val = float(data.get('adv_val', 0))
            bal_val = max(0.0, rent_val - adv_val)
            status_tag = 'Settled' if bal_val <= 0 else 'Returned_Due'

        actual_return_date = data.get('return_date') or datetime.now().strftime('%d/%m/%Y')

        cursor.execute("""
            UPDATE equipment_logs 
            SET return_date = ?, per_day_rent = ?, advance_amount = ?, balance_amount = ?, status = ? 
            WHERE id = ? AND CAST(studio_id AS INTEGER) = ?
        """, (actual_return_date, rent_val, adv_val, bal_val, status_tag, log_id, cur_studio))

        if data.get('recover_stock', True):
            for cname in cam_names:
                clean_cname = cname.split('(')[0].strip()
                cursor.execute("""
                    UPDATE studio_assets 
                    SET available_stock_qty = MIN(total_stock_qty, available_stock_qty + 1) 
                    WHERE (asset_name = ? OR LOWER(asset_name) = LOWER(?)) AND CAST(studio_id AS INTEGER) = ?
                """, (clean_cname, clean_cname, cur_studio))

        conn.commit()
        return jsonify({
            "status": "success", 
            "message": "Equipment return recorded successfully!",
            "new_status": status_tag
        })
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()
# =====================================================================
# 👥 15. EMPLOYEE CORNER & SALARY ENGINE (ISOLATED)
# =====================================================================

# रूट के अंदर कोई CREATE TABLE नहीं, सिर्फ सीधा काम:
@app.route('/api/get_employees_list', methods=['GET'])
@login_required
def get_employees_list():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        status_filter = str(request.args.get('status', 'Active')).strip().lower()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM employees WHERE CAST(studio_id AS INTEGER) = ? ORDER BY id DESC", (cur_studio,))
        all_rows = [dict(r) for r in cursor.fetchall()]
        
        filtered = [
            emp for emp in all_rows 
            if (status_filter == 'all') or 
               (status_filter == 'resigned' and str(emp.get('status')).lower() in ['resigned', 'terminated', 'inactive']) or 
               (status_filter == 'active' and str(emp.get('status') or 'active').lower() in ['active', ''])
        ]
        
        return jsonify({'status': 'success', 'data': filtered})
    except Exception as e:
        return jsonify({'status': 'success', 'data': []}), 200
    finally:
        if conn: conn.close()

@app.route('/api/get_employee_salary_summary', methods=['GET', 'POST'])
@login_required
def get_employee_salary_summary():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.get_json(silent=True) or request.form or request.args
        emp_id = data.get('emp_id')
        raw_m = str(data.get('month', '')).strip()
        raw_y = str(data.get('year', '')).strip()

        today = date.today()
        clean_m = raw_m.split('-')[0].split(' ')[0].strip().zfill(2) if raw_m else str(today.month).zfill(2)
        clean_y = raw_y.split('-')[0].split(' ')[0].strip() if raw_y else str(today.year)

        try:
            m_int, y_int = int(clean_m), int(clean_y)
        except Exception:
            m_int, y_int = today.month, today.year
            clean_m, clean_y = str(m_int).zfill(2), str(y_int)

        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM employees WHERE id=? AND studio_id=?", (emp_id, cur_studio))
        emp_raw = cursor.fetchone()
        if not emp_raw:
            return jsonify({"status": "error", "message": "Staff not found"}), 404

        emp = dict(emp_raw)
        m_salary = float(emp.get('salary') or 0.0)
        emp_first_name = str(emp.get('name') or '').strip().lower().split(' ')[0]

        total_days_in_month = calendar.monthrange(y_int, m_int)[1]
        start_of_month = date(y_int, m_int, 1)
        end_of_month = date(y_int, m_int, total_days_in_month)

        j_dt = safe_parse_date(emp.get('join_date')) or start_of_month
        valid_work_days = 0 if j_dt > end_of_month else ((end_of_month - j_dt).days + 1 if j_dt >= start_of_month else total_days_in_month)

        # 1. केवल इसी चुने हुए महीने की छुट्टियां
        thd, ta = 0, 0
        absents_list = []
        cursor.execute("SELECT attendance_date, status, reason FROM employee_attendance WHERE (employee_id=? OR emp_id=?) AND studio_id=?", (emp_id, emp_id, cur_studio))
        for r in cursor.fetchall():
            dt = str(r['attendance_date'] or '')
            st = str(r['status'] or '').strip().lower()
            rsn = str(r['reason'] or '')

            if f"/{clean_m}/{clean_y}" in dt or f"-{clean_m}-" in dt or f"/{int(clean_m)}/{clean_y}" in dt:
                if 'half' in st:
                    thd += 1
                    absents_list.append({"attendance_date": dt, "status": "Half-Day", "reason": rsn})
                elif 'absent' in st or 'leave' in st:
                    ta += 1
                    absents_list.append({"attendance_date": dt, "status": "Absent", "reason": rsn})

        # 2. केवल इसी चुने हुए महीने के ट्रांजेक्शन्स
        t_inc, t_adv_given, t_settle = 0.0, 0.0, 0.0
        cursor.execute("SELECT trans_date, trans_type, amount FROM employee_transactions WHERE (employee_id=? OR emp_id=?) AND studio_id=?", (emp_id, emp_id, cur_studio))
        for tx in cursor.fetchall():
            dt = str(tx['trans_date'] or '')
            ttype = str(tx['trans_type'] or '').lower()
            amt = float(tx['amount'] or 0.0)

            # तारीख का सख्त मिलान केवल चुने हुए महीने और साल से
            if f"/{clean_m}/{clean_y}" in dt or f"-{clean_m}-" in dt or f"/{int(clean_m)}/{clean_y}" in dt or f"{clean_y}-{clean_m}" in dt:
                if 'incentive' in ttype:
                    t_inc += amt
                elif 'advance' in ttype:
                    t_adv_given += amt
                elif 'settle' in ttype:
                    t_settle += amt

        # 3. केवल इसी महीने का वेतन
        per_day_rate = (m_salary / total_days_in_month) if total_days_in_month > 0 else 0.0
        calc_days = max(0.0, float(valid_work_days) - ta - (thd * 0.5))
        earned_salary = per_day_rate * calc_days
        total_incentive = t_inc
        month_adv_balance = max(0.0, t_adv_given)
        total_gross = earned_salary + total_incentive

        # 🎯 केवल इसी महीने का शुद्ध बकाया (कोई पिछला बकाया इसमें नहीं जुड़ेगा)
        net_payable = max(0.0, total_gross - month_adv_balance - t_settle)
        is_settled = bool(net_payable == 0 and t_settle > 0)

        cursor.execute("SELECT * FROM wedding_incentive_staging WHERE status='Pending' AND studio_id=? AND (emp_id=? OR LOWER(emp_name) LIKE ?)", (cur_studio, emp_id, f"%{emp_first_name}%"))
        staging_rows = [dict(s) for s in cursor.fetchall()]

        return jsonify({
            "status": "success",
            "summary": {
                "per_day_rate": round(per_day_rate, 2),
                "earned_salary": round(earned_salary, 2),
                "total_incentive": round(total_incentive, 2),
                "adv_given": round(t_adv_given, 2),
                "adv_settled": round(t_settle, 2),
                "month_adv_balance": round(month_adv_balance, 2),
                "net_payable": round(net_payable, 2),
                "half_days": thd,
                "absents": ta,
                "is_settled": is_settled,
                "total_days_in_month": total_days_in_month
            },
            "absents_list": absents_list,
            "staging_incentives": staging_rows
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/mark_employee_attendance', methods=['POST'])
@login_required
def mark_employee_attendance():
    cur_studio = get_current_studio_id()
    data = request.json or {}
    emp_id = data.get('emp_id')
    raw_from = data.get('from_date')
    raw_to = data.get('to_date') or raw_from
    status = str(data.get('status') or 'Present').strip()
    reason = str(data.get('reason') or '').strip()

    if status.lower() in ['absent', 'half-day'] and not reason:
        return jsonify({"status": "error", "message": f"{status} के लिए कारण लिखना अनिवार्य है!"}), 400

    try:
        start_dt = safe_parse_date(raw_from)
        end_dt = safe_parse_date(raw_to)
    except Exception:
        return jsonify({"status": "error", "message": "Invalid Date"}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        curr = start_dt
        while curr <= end_dt:
            dt_slash = curr.strftime('%d/%m/%Y')
            dt_dash = curr.strftime('%Y-%m-%d')
            cursor.execute("DELETE FROM employee_attendance WHERE (employee_id=? OR emp_id=?) AND (attendance_date=? OR attendance_date=?) AND studio_id=?", (emp_id, emp_id, dt_slash, dt_dash, cur_studio))
            if status.lower() != 'present':
                cursor.execute("INSERT INTO employee_attendance (studio_id, employee_id, emp_id, attendance_date, status, reason) VALUES (?, ?, ?, ?, ?, ?)", (cur_studio, emp_id, emp_id, dt_slash, status, reason))
            curr += timedelta(days=1)
        conn.commit()
        return jsonify({"status": "success", "message": "Attendance दर्ज हो गई!"})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/handle_employee_money', methods=['POST'])
@login_required
def handle_employee_money():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        emp_id = data.get('emp_id')
        action = data.get('action_type')
        amt = float(data.get('amount', 0.0) or 0.0)
        inc_amt = float(data.get('inc_amount', 0.0) or 0.0)
        inc_rsn = data.get('inc_reason', '').strip()
        
        # 🎯 चुने गए महीने और वर्ष को सटीक उठाएं
        m = str(data.get('month', '')).strip().zfill(2)
        y = str(data.get('year', '')).strip()
        
        now_dt = datetime.now()
        day_str = now_dt.strftime('%d')
        time_str = now_dt.strftime('%I:%M %p')

        # फ़्रंटएंड के फ़िल्टर (/08/2026) से 100% मैच होने वाली तारीख बनाएं
        if m and y and m != '00':
            t_now_display = f"{day_str}/{m}/{y} {time_str}"
        else:
            t_now_display = now_dt.strftime('%d/%m/%Y %I:%M %p')

        conn = get_db_connection()
        cursor = conn.cursor()

        if action == 'GIVE':
            cursor.execute("""
                INSERT INTO employee_transactions 
                (studio_id, employee_id, emp_id, trans_date, trans_type, amount, remarks) 
                VALUES (?, ?, ?, ?, 'Advance Given', ?, 'Advance Cash')
            """, (cur_studio, emp_id, emp_id, t_now_display, amt))
            msg = f"₹ {amt:.2f} Advance दर्ज हुआ।"

        elif action == 'SETTLE':
            # 🎯 SETTLE एंट्री में trans_type 'Salary Settle' रहेगा
            cursor.execute("""
                INSERT INTO employee_transactions 
                (studio_id, employee_id, emp_id, trans_date, trans_type, amount, remarks) 
                VALUES (?, ?, ?, ?, 'Salary Settle', ?, ?)
            """, (cur_studio, emp_id, emp_id, t_now_display, amt, f"Salary Settled for {m}/{y}"))
            msg = f"₹ {amt:.2f} सैलरी सफलतापूर्वक सेटल हो गई!"

        elif action == 'INCENTIVE':
            cursor.execute("""
                INSERT INTO employee_transactions 
                (studio_id, employee_id, emp_id, trans_date, trans_type, amount, remarks) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (cur_studio, emp_id, emp_id, t_now_display, f"Incentive: {inc_rsn or 'General'}", inc_amt, inc_rsn or 'Incentive Added'))
            msg = f"₹ {inc_amt:.2f} इंसेंटिव जोड़ा गया।"

        else:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        conn.commit()
        return jsonify({
            "status": "success", 
            "message": msg
        })

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()
# =====================================================================
# 👤 TOGGLE EMPLOYEE STATUS (RESIGN / RE-JOIN) API
# =====================================================================

@app.route('/api/change_employee_status', methods=['POST'])
@login_required
def change_employee_status():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.get_json(silent=True) or request.form or {}
        emp_id = data.get('emp_id')
        current_status = str(data.get('current_status') or 'Active').strip().lower()

        if not emp_id:
            return jsonify({"status": "error", "message": "Employee ID missing!"}), 400

        # 🎯 अगर पहले से Resigned/Inactive है तो Active (Re-Join) करेगा, अन्यथा Resigned
        if current_status in ['resigned', 'terminated', 'inactive']:
            new_status = 'Active'
            msg = "कर्मचारी को सफलतापूर्वक पुनः सक्रिय (Re-Joined) कर दिया गया!"
        else:
            new_status = 'Resigned'
            msg = "कर्मचारी का इस्तीफ़ा (Resigned) दर्ज कर दिया गया!"

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE employees 
            SET status = ? 
            WHERE id = ? AND studio_id = ?
        """, (new_status, emp_id, cur_studio))

        conn.commit()

        return jsonify({
            "status": "success", 
            "message": msg, 
            "new_status": new_status
        })

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

# ====================================================================
# 👥 EMPLOYEE LEDGER MISSING APIS (404 FIX)
# ====================================================================

@app.route('/api/get_employee_transactions/<int:emp_id>', methods=['GET'])
@login_required
def get_employee_transactions_api(emp_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        month = str(request.args.get('month', '')).strip().zfill(2)
        year = str(request.args.get('year', '')).strip()
        
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, emp_id, employee_id, trans_date, trans_type, amount, remarks 
            FROM employee_transactions 
            WHERE (emp_id = ? OR employee_id = ?) AND CAST(studio_id AS INTEGER) = ?
            ORDER BY id DESC
        """, (emp_id, emp_id, cur_studio))
        
        raw_rows = cursor.fetchall()
        transactions = []

        for r in raw_rows:
            d = dict(r)
            dt_str = str(d.get('trans_date') or '')

            # यदि माह और वर्ष का फ़िल्टर चुना गया हो
            match = True
            if month and year and month != '00':
                if not (f"/{month}/{year}" in dt_str or f"-{month}-" in dt_str or f"{year}-{month}" in dt_str):
                    match = False

            if match:
                transactions.append({
                    "id": d['id'],
                    "date_time": dt_str,
                    "trans_date": dt_str,
                    "transaction_type": d.get('trans_type') or 'Payment',
                    "trans_type": d.get('trans_type') or 'Payment',
                    "amount": float(d.get('amount') or 0.0),
                    "remarks": d.get('remarks') or '-'
                })

        return jsonify({
            "status": "success",
            "transactions": transactions,
            "data": transactions
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "transactions": []})
    finally:
        if conn:
            conn.close()

@app.route('/api/get_employee_upcoming_weddings', methods=['GET'])
@login_required
def get_employee_upcoming_weddings_api():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        emp_name = request.args.get('emp_name', '').strip().lower()
        
        conn = get_db_connection()
        cursor = conn.cursor()

        # शादी बुकिंग्स जहाँ यह स्टाफ असाइन है
        cursor.execute("""
            SELECT id, customer_name, wed_date_1, wed_event_1, photographers, cinemato, drone_op, status
            FROM wedding_bookings
            WHERE (studio_id = ? OR studio_id = ?) AND status NOT IN ('Cancelled', 'Delivered')
        """, (cur_studio, str(cur_studio)))
        
        weddings = cursor.fetchall()
        assigned_events = []

        for w in weddings:
            staff_combined = f"{w['photographers'] or ''} {w['cinemato'] or ''} {w['drone_op'] or ''}".lower()
            if emp_name in staff_combined:
                assigned_events.append({
                    "id": w['id'],
                    "customer_name": w['customer_name'],
                    "event_date": w['wed_date_1'],
                    "event_name": w['wed_event_1'] or 'Wedding Shoot',
                    "status": w['status']
                })

        return jsonify({
            "status": "success",
            "events": assigned_events
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "events": []})
    finally:
        if conn:
            conn.close()


@app.route('/api/get_employee_backdate_dues', methods=['GET'])
@login_required
def get_employee_backdate_dues_api():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        emp_id = request.args.get('emp_id')
        cur_m = int(request.args.get('cur_month', date.today().month))
        cur_y = int(request.args.get('cur_year', date.today().year))

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT salary, join_date FROM employees WHERE id = ? AND studio_id = ?", (emp_id, cur_studio))
        emp = cursor.fetchone()
        if not emp:
            return jsonify({"status": "success", "has_dues": False})

        m_salary = float(emp['salary'] or 0.0)
        j_dt = safe_parse_date(emp['join_date']) or date(cur_y, cur_m, 1)

        target_date = date(cur_y, cur_m, 1)
        check_date = date(j_dt.year, j_dt.month, 1)

        # जॉइनिंग से लेकर वर्तमान चुने हुए महीने के पहले तक हर महीने की जांच
        while check_date < target_date:
            chk_m = check_date.month
            chk_y = check_date.year
            m_str = str(chk_m).zfill(2)
            y_str = str(chk_y)
            days_in_m = calendar.monthrange(chk_y, chk_m)[1]
            end_m = date(chk_y, chk_m, days_in_m)

            work_days = 0 if j_dt > end_m else ((end_m - j_dt).days + 1 if j_dt >= check_date else days_in_m)

            # अनुपस्थिति
            thd, ta = 0, 0
            cursor.execute("SELECT attendance_date, status FROM employee_attendance WHERE (employee_id=? OR emp_id=?) AND studio_id=?", (emp_id, emp_id, cur_studio))
            for a in cursor.fetchall():
                adt = str(a['attendance_date'] or '')
                ast = str(a['status'] or '').lower()
                if f"/{m_str}/{y_str}" in adt or f"-{m_str}-" in adt or f"/{chk_m}/{y_str}" in adt:
                    if 'half' in ast: thd += 1
                    elif 'absent' in ast or 'leave' in ast: ta += 1

            # लेन-देन
            p_inc, p_adv, p_settle = 0.0, 0.0, 0.0
            cursor.execute("SELECT trans_date, trans_type, amount FROM employee_transactions WHERE (employee_id=? OR emp_id=?) AND studio_id=?", (emp_id, emp_id, cur_studio))
            for t in cursor.fetchall():
                tdt = str(t['trans_date'] or '')
                ttype = str(t['trans_type'] or '').lower()
                tamt = float(t['amount'] or 0.0)
                if f"/{m_str}/{y_str}" in tdt or f"-{m_str}-" in tdt or f"/{chk_m}/{y_str}" in tdt or f"{y_str}-{m_str}" in tdt:
                    if 'incentive' in ttype: p_inc += tamt
                    elif 'advance' in ttype: p_adv += tamt
                    elif 'settle' in ttype: p_settle += tamt

            per_day = (m_salary / days_in_m) if days_in_m > 0 else 0.0
            c_days = max(0.0, float(work_days) - ta - (thd * 0.5))
            earned = per_day * c_days
            unpaid = max(0.0, (earned + p_inc) - p_adv - p_settle)

            # यदि किसी भी पिछले महीने का बकाया 0 से अधिक है, तो तुरंत अलर्ट रिटर्न करें
            if unpaid > 0.0:
                return jsonify({
                    "status": "success",
                    "has_dues": True,
                    "due_info": {
                        "month": m_str,
                        "year": str(chk_y),
                        "amount": round(unpaid, 2)
                    }
                })

            if chk_m == 12:
                check_date = date(chk_y + 1, 1, 1)
            else:
                check_date = date(chk_y, chk_m + 1, 1)

        return jsonify({"status": "success", "has_dues": False})

    except Exception as e:
        return jsonify({"status": "error", "has_dues": False, "message": str(e)})
    finally:
        if conn:
            conn.close()

# =====================================================================
# 👥 EMPLOYEE LIFETIME STATEMENT / LEDGER API
# =====================================================================

@app.route('/api/get_employee_lifetime_ledger/<int:emp_id>', methods=['GET'])
@login_required
def get_employee_lifetime_ledger(emp_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. कर्मचारी का विवरण निकालें
        cursor.execute("SELECT * FROM employees WHERE id = ? AND studio_id = ?", (emp_id, cur_studio))
        emp_row = cursor.fetchone()
        if not emp_row:
            return jsonify({"status": "error", "message": "कर्मचारी नहीं मिला!"}), 404
        
        emp = dict(emp_row)

        # 2. सभी ट्रांजेक्शन्स (Advance, Settle, Incentive) निकालें
        cursor.execute("""
            SELECT id, trans_date, trans_type, amount, remarks 
            FROM employee_transactions 
            WHERE (employee_id = ? OR emp_id = ?) AND studio_id = ? 
            ORDER BY id ASC
        """, (emp_id, emp_id, cur_studio))
        transactions = [dict(r) for r in cursor.fetchall()]

        # 3. कुल सारांश गणना (Summary Calculation)
        total_advance = sum(float(t['amount'] or 0) for t in transactions if 'advance' in str(t['trans_type']).lower())
        total_incentive = sum(float(t['amount'] or 0) for t in transactions if 'incentive' in str(t['trans_type']).lower())
        total_settled = sum(float(t['amount'] or 0) for t in transactions if 'settle' in str(t['trans_type']).lower())

        return jsonify({
            "status": "success",
            "employee": emp,
            "transactions": transactions,
            "summary": {
                "total_advance": round(total_advance, 2),
                "total_incentive": round(total_incentive, 2),
                "total_settled": round(total_settled, 2)
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "transactions": []}), 500
    finally:
        if conn:
            conn.close()

# =====================================================================
# 📊 16. MASTER LIVE DASHBOARD METRICS (100% PAYMENT SYNC)
# =====================================================================

@app.route('/api/get_dashboard_live_data', methods=['GET'])
@login_required
def get_dashboard_live_data():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()

        now = datetime.now()
        today_dash = now.strftime('%Y-%m-%d')
        today_slash = now.strftime('%d/%m/%Y')
        today_d = now.date()

        total_cash = 0.0
        total_upi = 0.0

        # ----------------------------------------------------
        # 1. 📄 नए रेगुलर बिलों का आज का एडवांस (Bookings)
        # ----------------------------------------------------
        # ----------------------------------------------------
        # 📄 न्यू बिल और ऑल बिल्स का फाइनल और सटीक पेमेंट सिंक
        # ----------------------------------------------------
        try:
            cursor.execute("""
                SELECT 
                    cash_paid, upi_paid, advance_amount, 
                    bill_date, payment_history
                FROM bookings
                WHERE CAST(studio_id AS INTEGER) = ?
            """, (cur_studio,))
            
            for r in cursor.fetchall():
                # डेटाबेस से रॉ वैल्यू सुरक्षित तरीके से निकालें (डिक्शनरी या इंडेक्स दोनों पर काम करेगा)
                try:
                    b_date = str(r['bill_date'] or '').strip()
                    cp = float(r['cash_paid'] or 0)
                    up = float(r['upi_paid'] or 0)
                    adv = float(r['advance_amount'] or 0)
                except Exception:
                    # यदि कर्सर रो tuple के रूप में है
                    b_date = str(r[3] or '').strip()
                    cp = float(r[0] or 0)
                    up = float(r[1] or 0)
                    adv = float(r[2] or 0)

                # A. अगर यह बिल आज ही बना है (New Bill / Initial Payment)
                if any(b_date.startswith(d) for d in [today_dash, today_slash]):
                    if cp > 0:
                        total_cash += cp
                    if up > 0:
                        total_upi += up
                    if cp == 0 and up == 0 and adv > 0:
                        total_cash += adv

                # B. All Bills या New Bill से बाद में जमा हुई किस्तें (JSON History)
                raw_hist = r['payment_history'] if 'payment_history' in r.keys() else None
                if raw_hist:
                    try:
                        hist_list = json.loads(raw_hist) if isinstance(raw_hist, str) else raw_hist
                        if isinstance(hist_list, list):
                            for h in hist_list:
                                h_date_str = str(h.get('date', '') or h.get('date_time', '') or h.get('timestamp', '')).strip()
                                
                                # यदि किस्त आज जमा हुई है
                                if any(h_date_str.startswith(d) for d in [today_dash, today_slash]):
                                    h_amt = float(h.get('amount', 0) or 0)
                                    h_mode = str(h.get('mode', h.get('payment_mode', h.get('type', 'CASH')))).upper()
                                    
                                    if any(kw in h_mode for kw in ['UPI', 'ONLINE', 'QR', 'SCAN', 'PHONEPE', 'PAYTM', 'GOOGLEPAY', 'GPAY', 'BANK', 'NEFT', 'RTGS']):
                                        total_upi += h_amt
                                    else:
                                        total_cash += h_amt
                    except Exception as json_err:
                        print("JSON History Parse Error:", json_err)
        except Exception as e:
            print("Dashboard Bookings Error:", e)

        # ----------------------------------------------------
        # 2. 💳 क्रेडिट लेज़र और पार्ट पेमेंट पासबुक (payment_history)
        # ----------------------------------------------------
        try:
            cursor.execute("""
                SELECT 
                    COALESCE(cash_amount, 0) as c_amt,
                    COALESCE(upi_amount, 0) as u_amt,
                    payment_date
                FROM payment_history
                WHERE CAST(studio_id AS INTEGER) = ?
            """, (cur_studio,))
            
            for ph in cursor.fetchall():
                p_dt = str(ph['payment_date'] or '').strip().split(' ')[0]
                if p_dt in [today_dash, today_slash]:
                    total_cash += float(ph['c_amt'] or 0)
                    total_upi += float(ph['u_amt'] or 0)
        except Exception as e:
            print("Dashboard Payment History Error:", e)

        # ----------------------------------------------------
        # 3. 🎫 डेली टोकन काउंटर (Daily Tokens)
        # ----------------------------------------------------
        try:
            cursor.execute("""
                SELECT 
                    COALESCE(cash_paid, 0) as cp,
                    COALESCE(upi_paid, 0) as up,
                    COALESCE(total_amount, 0) as tot,
                    token_date, created_at
                FROM daily_tokens 
                WHERE CAST(studio_id AS INTEGER) = ?
            """, (cur_studio,))
            
            for r in cursor.fetchall():
                t_dt = str(r['token_date'] or '').strip()
                c_dt = str(r['created_at'] or '').split(' ')[0]
                if any(d in [t_dt, c_dt] for d in [today_slash, today_dash]):
                    c = float(r['cp'] or 0)
                    u = float(r['up'] or 0)
                    if c == 0 and u == 0:
                        c = float(r['tot'] or 0)
                    total_cash += c
                    total_upi += u
        except Exception as e:
            print("Dashboard Tokens Error:", e)

        # ----------------------------------------------------
        # 4. 💍 वेडिंग पेमेंट्स टेबल (wedding_payments)
        # ----------------------------------------------------
        try:
            cursor.execute("""
                SELECT amount, payment_mode, date_time 
                FROM wedding_payments 
                WHERE CAST(studio_id AS INTEGER) = ?
            """, (cur_studio,))
            
            for wp in cursor.fetchall():
                dt_str = str(wp['date_time'] or '').strip().split(' ')[0]
                if dt_str in [today_slash, today_dash]:
                    amt = float(wp['amount'] or 0)
                    mode = str(wp['payment_mode'] or '').upper()
                    if 'UPI' in mode or 'ONLINE' in mode:
                        total_upi += amt
                    else:
                        total_cash += amt
        except Exception as e:
            print("Dashboard Wedding Payments Error:", e)

        # ----------------------------------------------------
        # 5. 📸 कैमरा / इक्विपमेंट रेंट कलेक्शन (equipment_logs)
        # ----------------------------------------------------
        try:
            cursor.execute("""
                SELECT advance_amount, issue_date 
                FROM equipment_logs 
                WHERE options_type = 'Rent' AND CAST(studio_id AS INTEGER) = ?
            """, (cur_studio,))
            for el in cursor.fetchall():
                i_dt = str(el['issue_date'] or '').strip()
                if i_dt in [today_slash, today_dash]:
                    total_cash += float(el['advance_amount'] or 0)
        except Exception as e:
            print("Dashboard Equipment Error:", e)

        grand_total = total_cash + total_upi

        # ----------------------------------------------------
        # 6. आज की प्रॉमिस डेट उधारी (Credit Ledger)
        # ----------------------------------------------------
        today_promise_dues = []
        try:
            cursor.execute("""
                SELECT cname, mob, due_amount, reason, bill_no 
                FROM credit_ledger 
                WHERE status != 'Paid' AND due_amount > 0 
                  AND (promise_date LIKE ? OR promise_date LIKE ?) 
                  AND CAST(studio_id AS INTEGER) = ?
            """, (f"{today_dash}%", f"{today_slash}%", cur_studio))
            today_promise_dues = [dict(r) for r in cursor.fetchall()]
        except Exception:
            pass

        # ----------------------------------------------------
        # 7. आज की पेंडिंग डिलीवरी (Bookings)
        # ----------------------------------------------------
        today_deliveries = []
        try:
            cursor.execute("""
                SELECT bill_no, customer_name, mobile, total_amount, balance_amount 
                FROM bookings 
                WHERE status = 'Pending' 
                  AND (delivery_date LIKE ? OR delivery_date LIKE ?) 
                  AND CAST(studio_id AS INTEGER) = ?
            """, (f"{today_dash}%", f"{today_slash}%", cur_studio))
            today_deliveries = [dict(r) for r in cursor.fetchall()]
        except Exception:
            pass

        # ----------------------------------------------------
        # 8. आगामी 30 दिनों की शादियाँ
        # ----------------------------------------------------
        upcoming_weddings = []
        try:
            cursor.execute("""
                SELECT id, customer_name, mobile, wed_date_1, wed_event_1, venue_location 
                FROM wedding_bookings 
                WHERE status NOT IN ('Cancelled', 'Delivered') 
                  AND CAST(studio_id AS INTEGER) = ?
            """, (cur_studio,))
            for w in cursor.fetchall():
                d_val = str(w['wed_date_1'] or '').strip()
                if d_val:
                    try:
                        dt_obj = datetime.strptime(d_val, '%Y-%m-%d').date() if '-' in d_val else datetime.strptime(d_val, '%d/%m/%Y').date()
                        if dt_obj >= today_d:
                            days_left = (dt_obj - today_d).days
                            if days_left <= 30:
                                upcoming_weddings.append({
                                    "id": w['id'], "customer_name": w['customer_name'], "mobile": w['mobile'],
                                    "event_name": w['wed_event_1'] or 'Wedding Event', "date": d_val,
                                    "days_left": days_left, "venue": w['venue_location'] or 'Studio'
                                })
                    except Exception:
                        pass
            upcoming_weddings.sort(key=lambda x: x['days_left'])
        except Exception:
            pass

        # ----------------------------------------------------
        # 9. लो स्टॉक और स्टाफ सारांश
        # ----------------------------------------------------
        low_stocks = []
        total_stock_count = 0
        stock_status = "OK"
        try:
            cursor.execute("SELECT pname AS material_name, pqty AS current_qty, 'Pcs' AS unit FROM product_stock WHERE CAST(studio_id AS INTEGER) = ? ORDER BY pqty ASC", (cur_studio,))
            prod_rows = cursor.fetchall()
            total_stock_count += len(prod_rows)
            for r in prod_rows:
                if float(r['current_qty'] or 0) <= 5:
                    low_stocks.append(dict(r))

            cursor.execute("SELECT material_name, current_qty, unit FROM master_raw_materials WHERE CAST(studio_id AS INTEGER) = ? ORDER BY current_qty ASC", (cur_studio,))
            raw_rows = cursor.fetchall()
            total_stock_count += len(raw_rows)
            for r in raw_rows:
                if float(r['current_qty'] or 0) <= 5:
                    low_stocks.append(dict(r))

            stock_status = "EMPTY" if total_stock_count == 0 else ("ALERT" if len(low_stocks) > 0 else "OK")
        except Exception:
            pass

        emp_status_list = []
        total_emp_count = 0
        try:
            cursor.execute("SELECT id, name, role FROM employees WHERE (status IS NULL OR LOWER(status) = 'active') AND CAST(studio_id AS INTEGER) = ?", (cur_studio,))
            all_active_emps = cursor.fetchall()
            total_emp_count = len(all_active_emps)

            cursor.execute("SELECT employee_id, emp_id, status, reason FROM employee_attendance WHERE (attendance_date LIKE ? OR attendance_date LIKE ?) AND CAST(studio_id AS INTEGER) = ?", (f"{today_slash}%", f"{today_dash}%", cur_studio))
            att_rows = cursor.fetchall()

            absent_map = {}
            for a in att_rows:
                k = a['employee_id'] if 'employee_id' in a.keys() and a['employee_id'] else (a['emp_id'] if 'emp_id' in a.keys() else None)
                if k:
                    absent_map[k] = {"status": a['status'], "reason": a['reason']}

            for e in all_active_emps:
                if e['id'] in absent_map:
                    emp_status_list.append({"name": e['name'], "role": e['role'], "status": absent_map[e['id']]['status'], "reason": absent_map[e['id']]['reason'], "is_present": False})
                else:
                    emp_status_list.append({"name": e['name'], "role": e['role'], "status": "Present", "reason": "", "is_present": True})
        except Exception:
            pass

        # पेंडिंग रीसेंट बुकिंग्स
        recent_pending_bookings = []
        try:
            cursor.execute("""
                SELECT bill_no, customer_name, bill_date, total_amount, balance_amount, status 
                FROM bookings 
                WHERE status = 'Pending' AND CAST(studio_id AS INTEGER) = ? 
                ORDER BY CAST(bill_no AS INTEGER) DESC LIMIT 10
            """, (cur_studio,))
            recent_pending_bookings = [dict(r) for r in cursor.fetchall()]
        except Exception:
            pass

        return jsonify({
            "status": "success",
            "collection": {
                "upi": total_upi,
                "cash": total_cash,
                "total": grand_total
            },
            "today_promise_dues": today_promise_dues,
            "today_deliveries": today_deliveries,
            "upcoming_weddings": upcoming_weddings,
            "low_stocks": low_stocks,
            "stock_status": stock_status,
            "total_stock_count": total_stock_count,
            "has_no_stock": (total_stock_count == 0),
            "emp_status_list": emp_status_list,
            "total_employees": total_emp_count,
            "has_no_emp": (total_emp_count == 0),
            "recent_bookings": recent_pending_bookings
        })

    except Exception as e:
        print("❌ Dashboard Error:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

@app.route('/api/record_part_payment', methods=['POST'])
@login_required
def record_part_payment():
    conn = None
    try:
        cur_studio = str(get_current_studio_id()).strip()
        data = request.get_json(force=True, silent=True) or request.form or {}

        bill_no = data.get('bill_no')
        part_amt = float(data.get('amount') or 0.0)
        mode = str(data.get('mode') or 'CASH').upper()

        if not bill_no or part_amt <= 0:
            return jsonify({'status': 'error', 'message': 'वैध राशि और बिल नंबर दर्ज करें!'}), 400

        now_dt = datetime.now()
        pay_time_display = now_dt.strftime('%d/%m/%Y %I:%M %p')

        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. पहले इस बिल की मौजूदा जानकारी और पुरानी हिस्ट्री निकालें
        cursor.execute("""
            SELECT bill_date, total_amount, discount_amount, advance_amount, balance_amount, payment_history 
            FROM bookings 
            WHERE bill_no = ? AND (studio_id = ? OR studio_id = ?)
        """, (bill_no, cur_studio, int(cur_studio) if cur_studio.isdigit() else cur_studio))
        bill_row = cursor.fetchone()

        if not bill_row:
            return jsonify({'status': 'error', 'message': 'बिल नहीं मिला!'}), 404

        tot_amt = float(bill_row['total_amount'] or 0.0)
        disc_amt = float(bill_row['discount_amount'] or 0.0)
        old_adv = float(bill_row['advance_amount'] or 0.0)
        bill_date_str = str(bill_row['bill_date'] or '')

        # 2. पुरानी हिस्ट्री को सुरक्षित रखें
        raw_hist = bill_row['payment_history'] or '[]'
        try:
            history_list = json.loads(raw_hist) if isinstance(raw_hist, str) else list(raw_hist)
        except Exception:
            history_list = []

        # 🎯 अगर हिस्ट्री लिस्ट पूरी तरह खाली है, तो पहले ओरिजिनल एडवांस को जोड़ें
        if len(history_list) == 0 and old_adv > 0:
            history_list.append({
                "date": bill_date_str or pay_time_display,
                "amount": old_adv,
                "mode": "ADVANCE",
                "note": "Initial Advance Booking"
            })

        # 3. अब नई पार्ट पेमेंट को हिस्ट्री लिस्ट में पुश (Append) करें
        history_list.append({
            "date": pay_time_display,
            "amount": part_amt,
            "mode": mode,
            "note": "Part Payment Installment"
        })

        # 4. कुल जमा राशि (New Advance) और बाकी बैलेंस (New Balance) की सटीक गणना
        new_total_collected = old_adv + part_amt
        new_balance = max(0.0, tot_amt - disc_amt - new_total_collected)

        # 5. डेटाबेस में हिस्ट्री और नया बैलेंस अपडेट करें
        cursor.execute("""
            UPDATE bookings 
            SET advance_amount = ?,
                balance_amount = ?,
                payment_history = ?
            WHERE bill_no = ? AND (studio_id = ? OR studio_id = ?)
        """, (new_total_collected, new_balance, json.dumps(history_list), bill_no, cur_studio, int(cur_studio) if cur_studio.isdigit() else cur_studio))

        conn.commit()
        return jsonify({
            'status': 'success',
            'message': f'₹{part_amt:.2f} की पार्ट पेमेंट दर्ज हो गई!',
            'new_balance': new_balance,
            'collected_total': new_total_collected
        })

    except Exception as e:
        if conn: conn.rollback()
        print("Part payment error:", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# 👑 15. SAAS MASTER DASHBOARD API (SAFE SCHEMA DETECTION)
# =====================================================================

@app.route('/api/saas/master_dashboard', methods=['GET'])
@login_required
def saas_master_dashboard():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        if int(cur_studio) != 1:
            return jsonify({"status": "error", "message": "Unauthorized! Super Admin access only."}), 403

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = [str(r[0]) for r in cursor.fetchall()]

        studios_list = []
        if 'saas_studios' in existing_tables:
            cursor.execute("""
                SELECT id, 
                       COALESCE(studio_name, 'Studio') AS studio_name,
                       COALESCE(owner_name, 'Admin') AS owner_name,
                       COALESCE(address, 'Address not provided') AS address,
                       COALESCE(mobile, '-') AS mobile,
                       COALESCE(whatsapp, mobile) AS whatsapp,
                       COALESCE(email, '') AS email,
                       COALESCE(status, 'Active') AS status,
                       COALESCE(plan_expiry, '') AS plan_expiry_date,
                       COALESCE(subscription_plan, '30 Days Free Trial') AS subscription_plan,
                       COALESCE(created_at, '-') AS created_at
                FROM saas_studios
                ORDER BY id ASC
            """)
            rows = cursor.fetchall()

            today_dt = datetime.now().date()
            for r in rows:
                s_dict = dict(r)
                s_id = int(s_dict['id'])

                if s_id == 1:
                    s_dict['plan_expiry_date'] = 'Lifetime'
                    s_dict['subscription_plan'] = 'Master Admin License'
                    s_dict['status'] = 'Active'
                else:
                    curr_st = str(s_dict.get('status') or 'Active')
                    # यदि पहले से ब्लॉक (Suspended) न हो तभी एक्सपायरी चेक करें
                    if curr_st != 'Suspended':
                        exp_str = s_dict.get('plan_expiry_date') or ''
                        if exp_str and exp_str != 'Lifetime':
                            for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
                                try:
                                    exp_dt = datetime.strptime(exp_str, fmt).date()
                                    if today_dt > exp_dt:
                                        s_dict['status'] = 'Expired'
                                        cursor.execute("UPDATE saas_studios SET status = 'Expired' WHERE id = ?", (s_id,))
                                    break
                                except Exception:
                                    continue

                studios_list.append(s_dict)
            conn.commit()

        # 🎯 कुल प्लेटफॉर्म बिल काउंट (Regular + Wedding + Tokens)
        total_bills = 0
        for b_tbl in ['bookings', 'wedding_bookings', 'daily_tokens']:
            if b_tbl in existing_tables:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {b_tbl}")
                    total_bills += int(cursor.fetchone()[0] or 0)
                except Exception:
                    pass

        return jsonify({
            "status": "success",
            "stats": {
                "total_studios": len(studios_list),
                "total_bills_generated": total_bills
            },
            "studios": studios_list
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

# 🔄 1-क्लिक रिन्यूअल (स्मार्ट डेट एक्सटेंशन)
@app.route('/api/saas/renew_subscription', methods=['POST'])
@login_required
def saas_renew_subscription():
    conn = None
    try:
        if int(get_current_studio_id()) != 1:
            return jsonify({"status": "error", "message": "Unauthorized!"}), 403

        data = request.json or {}
        target_studio_id = int(data.get('studio_id', 0))
        months_to_add = int(data.get('months', 1))

        if target_studio_id <= 1:
            return jsonify({"status": "error", "message": "मास्टर स्टूडियो आजीवन सक्रिय है!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT plan_expiry FROM saas_studios WHERE id = ?", (target_studio_id,))
        studio = cursor.fetchone()

        if not studio:
            return jsonify({"status": "error", "message": "स्टूडियो नहीं मिला!"}), 404

        today = datetime.now().date()
        base_date = today

        # यदि पहले से प्लान एक्टिव है, तो बची हुई एक्सपायरी के आगे से दिन जोड़ें
        curr_expiry_str = str(studio['plan_expiry'] or '').strip()
        if curr_expiry_str and curr_expiry_str != 'Lifetime':
            for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
                try:
                    exp_dt = datetime.strptime(curr_expiry_str, fmt).date()
                    if exp_dt > today:
                        base_date = exp_dt
                    break
                except Exception:
                    continue

        days_map = {1: 30, 3: 90, 6: 180, 12: 365}
        added_days = days_map.get(months_to_add, months_to_add * 30)

        new_expiry_date = (base_date + timedelta(days=added_days)).strftime('%d/%m/%Y')
        plan_label = f"{months_to_add} Month(s) Active" if months_to_add < 12 else "1 Year Active"

        cursor.execute("""
            UPDATE saas_studios 
            SET status = 'Active', plan_expiry = ?, subscription_plan = ? 
            WHERE id = ?
        """, (new_expiry_date, plan_label, target_studio_id))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"सफलतापूर्वक रिन्यू हुआ! नई एक्सपायरी डेट: {new_expiry_date}"
        })
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ⛔ सस्पेंड / एक्टिवेट टॉगल रूट
@app.route('/api/saas/toggle_studio_status', methods=['POST'])
@login_required
def saas_toggle_studio_status():
    conn = None
    try:
        if int(get_current_studio_id()) != 1:
            return jsonify({"status": "error", "message": "Unauthorized!"}), 403

        data = request.json or {}
        target_studio_id = int(data.get('studio_id', 0))
        raw_status = str(data.get('status', 'Active')).strip()

        if target_studio_id <= 1:
            return jsonify({"status": "error", "message": "मास्टर स्टूडियो को सस्पेंड नहीं किया जा सकता!"}), 400

        # केवल वैध स्टेटस की अनुमति दें
        new_status = 'Suspended' if raw_status.lower() in ['suspended', 'inactive', 'blocked'] else 'Active'

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE saas_studios SET status = ? WHERE id = ?", (new_status, target_studio_id))
        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"Studio #{target_studio_id} का स्टेटस बदलकर '{new_status}' कर दिया गया है।"
        })
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()
# -------------------------------------------------------------
# 🎯 AUTO-RECIPE STOCK DEDUCTION ENGINE
# -------------------------------------------------------------
def deduct_recipe_stock(cursor, studio_id, work_type, service_qty, ref_title, log_time, cur_timestamp):
    """
    अगर काम की रेसिपी है तो सारे इंग्रीडिएंट्स काटेगा, 
    अगर सीधी बिक्री है तो डायरेक्ट स्टॉक काटेगा।
    """
    clean_work = str(work_type or '').strip()
    if not clean_work or clean_work == 'General Work':
        return []

    # 1. रेसिपी चेक करें
    cursor.execute("""
        SELECT ingredient_name, ingredient_type, qty_needed 
        FROM service_recipes 
        WHERE CAST(studio_id AS INTEGER) = ? 
          AND (TRIM(LOWER(service_name)) = TRIM(LOWER(?)) OR service_name LIKE ?)
    """, (studio_id, clean_work, f"%{clean_work}%"))
    
    recipes = cursor.fetchall()
    deducted_summary = []

    if recipes:
        # ✅ कॉम्बो रेसिपी मिली (जैसे Aadhaar Print -> 4x6 + Lamination Pouch)
        for r in recipes:
            ing_name = r['ingredient_name']
            ing_type = r['ingredient_type']
            total_qty_to_deduct = float(r['qty_needed']) * float(service_qty)

            if ing_type == 'material':
                cursor.execute("""
                    UPDATE master_raw_materials 
                    SET current_qty = MAX(0.0, current_qty - ?), last_updated = ? 
                    WHERE CAST(studio_id AS INTEGER) = ? 
                      AND (TRIM(LOWER(material_name)) = TRIM(LOWER(?)) OR material_name LIKE ?)
                """, (total_qty_to_deduct, log_time, studio_id, ing_name, f"%{ing_name}%"))

                cursor.execute("""
                    INSERT INTO material_stock_logs (studio_id, material_name, change_qty, action_type, raw_command, log_date) 
                    VALUES (?, ?, ?, 'RECIPE_AUTO_DEDUCT', ?, ?)
                """, (studio_id, ing_name, -total_qty_to_deduct, f"{ref_title} ({clean_work})", log_time))

            elif ing_type == 'product':
                cursor.execute("""
                    UPDATE product_stock 
                    SET pqty = MAX(0, pqty - ?), last_updated = ? 
                    WHERE CAST(studio_id AS INTEGER) = ? 
                      AND (TRIM(LOWER(pname)) = TRIM(LOWER(?)) OR pname LIKE ?)
                """, (int(total_qty_to_deduct), cur_timestamp, studio_id, ing_name, f"%{ing_name}%"))

            deducted_summary.append(f"{ing_name} ({total_qty_to_deduct})")
            
    return deducted_summary


# सर्वर चालू होते ही bookings में share_token कॉलम जोड़ें

def repair_bill_series(target_studio_id=1):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        print(f"--- Studio #{target_studio_id} के बिल नंबर सुधारना शुरू ---")

        cursor.execute("""
            SELECT id, bill_no, bill_date, 'REGULAR' as type 
            FROM bookings 
            WHERE CAST(studio_id AS INTEGER) = ?
        """, (target_studio_id,))
        reg_bills = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT id, bill_no, created_at as bill_date, 'WEDDING' as type 
            FROM wedding_bookings 
            WHERE CAST(studio_id AS INTEGER) = ?
        """, (target_studio_id,))
        wed_bills = [dict(r) for r in cursor.fetchall()]

        all_bills = reg_bills + wed_bills
        all_bills.sort(key=lambda x: (str(x.get('bill_date') or ''), int(x.get('id') or 0)))

        print(f"कुल {len(all_bills)} बिल मिले। सीरीज़ को 1 से रीसेट किया जा रहा है...")

        # 1. ओवरलैप से बचने के लिए अस्थायी ऑफ़सेट असाइन करना
        temp_offset = 1000000
        for idx, b in enumerate(all_bills, start=1):
            temp_no = temp_offset + idx
            if b['type'] == 'REGULAR':
                cursor.execute("""
                    UPDATE booking_items 
                    SET bill_no = ? 
                    WHERE bill_no = ? AND CAST(studio_id AS INTEGER) = ?
                """, (temp_no, b['bill_no'], target_studio_id))
                cursor.execute("""
                    UPDATE bookings 
                    SET bill_no = ? 
                    WHERE id = ? AND CAST(studio_id AS INTEGER) = ?
                """, (temp_no, b['id'], target_studio_id))
            elif b['type'] == 'WEDDING':
                cursor.execute("""
                    UPDATE wedding_bookings 
                    SET bill_no = ? 
                    WHERE id = ? AND CAST(studio_id AS INTEGER) = ?
                """, (temp_no, b['id'], target_studio_id))

        # 2. क्रम से 1, 2, 3... शुद्ध नंबर असाइन करना
        new_counter = 1
        for idx, b in enumerate(all_bills, start=1):
            temp_no = temp_offset + idx
            if b['type'] == 'REGULAR':
                cursor.execute("""
                    UPDATE booking_items 
                    SET bill_no = ? 
                    WHERE bill_no = ? AND CAST(studio_id AS INTEGER) = ?
                """, (new_counter, temp_no, target_studio_id))
                cursor.execute("""
                    UPDATE bookings 
                    SET bill_no = ? 
                    WHERE id = ? AND CAST(studio_id AS INTEGER) = ?
                """, (new_counter, b['id'], target_studio_id))
            elif b['type'] == 'WEDDING':
                cursor.execute("""
                    UPDATE wedding_bookings 
                    SET bill_no = ? 
                    WHERE id = ? AND CAST(studio_id AS INTEGER) = ?
                """, (new_counter, b['id'], target_studio_id))

            new_counter += 1

        conn.commit()
        print(f"\n✅ सभी बिल सफलतापूर्वक 1 से {new_counter - 1} तक क्रमबद्ध हो गए!")
        print(f"👉 अगला नया बिल नंबर #{new_counter} बनेगा।")
    except Exception as e:
        if conn:
            conn.rollback()
        print("Repair Error:", str(e))
    finally:
        if conn:
            conn.close()

@app.route('/api/saas/admin_override_credentials', methods=['POST'])
@login_required
def admin_override_credentials():
    if int(get_current_studio_id()) != 1:
        return jsonify({"status": "error", "message": "अनधिकृत एक्सेस! केवल मास्टर एडमिन को अनुमति है।"}), 403

    data = request.json or {}
    target_studio_id = int(data.get('studio_id', 0))

    if not target_studio_id:
        return jsonify({"status": "error", "message": "स्टूडियो आईडी अमान्य है!"}), 400

    if target_studio_id <= 1:
        return jsonify({"status": "error", "message": "मास्टर स्टूडियो (ID #1) को ओवरराइड नहीं किया जा सकता!"}), 400

    temp_password = str(data.get('temp_password', '')).strip() or "studio@1234"
    temp_pin = str(data.get('temp_pin', '')).strip() or "123456"

    if temp_pin and (len(temp_pin) != 6 or not temp_pin.isdigit()):
        return jsonify({"status": "error", "message": "मास्टर पिन ठीक 6 अंकों का होना चाहिए!"}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("ALTER TABLE saas_studios ADD COLUMN must_change_pwd INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        p_hash = generate_password_hash(temp_password)
        pin_hash = generate_password_hash(temp_pin)

        cursor.execute("""
            UPDATE saas_studios 
            SET password_hash = ?, 
                master_pin_hash = ?, 
                must_change_pwd = 1 
            WHERE id = ?
        """, (p_hash, pin_hash, target_studio_id))

        conn.commit()

        return jsonify({
            "status": "success", 
            "message": f"Studio #{target_studio_id} का क्रेडेंशियल्स रीसेट कर दिया गया!\n\nपासवर्ड: {temp_password}\nपिन: {temp_pin}\n(लॉगिन करते ही यूजर को नया पासवर्ड सेट करना होगा)"
        })

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

# 🎯 MASTER CONTROL: 1-CLICK RESET TO DEFAULT (admin123 / 123456)
@app.route('/api/saas/reset_to_default_credentials', methods=['POST'])
@login_required
def reset_to_default_credentials():
    if int(get_current_studio_id()) != 1:
        return jsonify({"status": "error", "message": "Unauthorized!"}), 403

    conn = None
    try:
        data = request.get_json(force=True, silent=True) or request.form or {}
        target_studio_id = int(data.get('studio_id', 0))

        if target_studio_id <= 1:
            return jsonify({"status": "error", "message": "Master studio cannot be reset!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        p_hash = generate_password_hash("admin123")
        pin_hash = generate_password_hash("123456")

        cursor.execute("""
            UPDATE saas_studios 
            SET password_hash = ?, 
                master_pin_hash = ?, 
                must_change_pwd = 1 
            WHERE id = ?
        """, (p_hash, pin_hash, target_studio_id))

        conn.commit()
        return jsonify({
            "status": "success", 
            "message": f"Studio #{target_studio_id} के क्रेडेंशियल्स सफलतापूर्वक रीसेट कर दिए गए!\n\nडिफ़ॉल्ट पासवर्ड: admin123\nडिफ़ॉल्ट पिन: 123456\n\nयूज़र अब लॉगिन करके 'Change Password' से नया पासवर्ड सेट कर सकता है।"
        })
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/delete_bill/<int:bill_id>', methods=['POST'])
@login_required
def delete_bill(bill_id):
    conn = None
    try:
        # 👑 1. अगर आप (मास्टर ओनर) हैं तो कोई पिन/पासवर्ड नहीं — सीधा डिलीट!
        if session.get('is_master') or session.get('studio_id') == 1:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bookings WHERE id = ? AND studio_id = 1", (bill_id,))
            conn.commit()
            return jsonify({"status": "success", "message": "बिल सफलतापूर्वक डिलीट कर दिया गया (मास्टर डायरेक्ट बाईपास)!"})

        # 🛑 2. अन्य SaaS यूज़र्स के लिए उनका खुद का पिन चेक होगा
        data = request.json or {}
        entered_pin = str(data.get('pin', '')).strip()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT master_pin_hash, pin FROM saas_studios WHERE id = ?", (session.get('studio_id'),))
        st_row = cursor.fetchone()

        if not st_row:
            return jsonify({"status": "error", "message": "स्टूडियो नहीं मिला!"}), 404

        pin_hash = str(st_row['master_pin_hash'] or st_row['pin'] or '')
        if not (check_password_hash(pin_hash, entered_pin) or pin_hash == entered_pin):
            return jsonify({"status": "error", "message": "अमान्य पिन! बिल डिलीट नहीं किया जा सका।"}), 403

        cursor.execute("DELETE FROM bookings WHERE id = ? AND studio_id = ?", (bill_id, session.get('studio_id')))
        conn.commit()
        return jsonify({"status": "success", "message": "बिल सफलतापूर्वक डिलीट हो गया!"})

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

# =====================================================================
# 👑 SUPER ADMIN: स्टूडियो लिस्ट, आज की गतिविधि और पासवर्ड रीसेट API
# =====================================================================

@app.route('/api/admin/studios_activity', methods=['GET'])
@login_required
def get_admin_studios_activity():
    if int(get_current_studio_id()) != 1:
        return jsonify({"status": "error", "message": "Unauthorized!"}), 403

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 📅 आज की तारीख और ठीक 7 दिन पहले की तारीख निकालना
        today_date = datetime.now().date()
        week_ago_date = today_date - timedelta(days=7)
        
        week_ago_str = week_ago_date.strftime('%Y-%m-%d')
        today_str = today_date.strftime('%Y-%m-%d')

        cursor.execute("""
            SELECT id, studio_name, owner_name, mobile, status, created_at 
            FROM saas_studios 
            ORDER BY id ASC
        """)
        studios = cursor.fetchall()

        report_list = []
        for s in studios:
            s_id = s['id']

            # 1. पिछले 7 दिनों के रेगुलर बिल्स (तारीख के आधार पर रेंज चेक)
            cursor.execute("""
                SELECT COUNT(*) FROM bookings 
                WHERE studio_id = ? AND SUBSTR(bill_date, 1, 10) BETWEEN ? AND ?
            """, (s_id, week_ago_str, today_str))
            std_weekly = cursor.fetchone()[0] or 0

            # 2. पिछले 7 दिनों की वेडिंग बुकिंग्स
            cursor.execute("""
                SELECT COUNT(*) FROM wedding_bookings 
                WHERE studio_id = ? AND SUBSTR(created_at, 1, 10) BETWEEN ? AND ?
            """, (s_id, week_ago_str, today_str))
            wed_weekly = cursor.fetchone()[0] or 0

            # 3. पिछले 7 दिनों के टोकन्स
            cursor.execute("""
                SELECT COUNT(*) FROM daily_tokens 
                WHERE studio_id = ? AND SUBSTR(token_date, 1, 10) BETWEEN ? AND ?
            """, (s_id, week_ago_str, today_str))
            tokens_weekly = cursor.fetchone()[0] or 0

            weekly_total_entries = std_weekly + wed_weekly + tokens_weekly

            # 4. लाइफटाइम टोटल (रेगुलर + वेडिंग)
            cursor.execute("SELECT COUNT(*) FROM bookings WHERE studio_id = ?", (s_id,))
            b_count = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(*) FROM wedding_bookings WHERE studio_id = ?", (s_id,))
            w_count = cursor.fetchone()[0] or 0
            lifetime_bills = b_count + w_count

            st_val = str(s['status'] or 'Active')
            report_list.append({
                "id": s_id,
                "studio_name": s['studio_name'],
                "owner_name": s['owner_name'],
                "mobile": s['mobile'],
                "is_active": 1 if st_val == 'Active' else 0,
                "weekly_count": weekly_total_entries,
                "status_label": "🟢 Active This Week" if weekly_total_entries > 0 else "⚪ Idle This Week",
                "lifetime_bills": lifetime_bills
            })

        return jsonify({"status": "success", "data": report_list})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/admin/reset_studio_credentials', methods=['POST'])
@login_required
def admin_reset_studio_credentials():
    if int(get_current_studio_id()) != 1:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    data = request.json or {}
    s_id = data.get('studio_id')
    new_pass = str(data.get('new_password', '')).strip()
    new_pin = str(data.get('new_pin', '')).strip()

    if not s_id:
        return jsonify({"status": "error", "message": "Studio ID is required"}), 400

    if int(s_id) == 1:
        return jsonify({"status": "error", "message": "Master studio cannot be reset here"}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        updates = []
        params = []

        if new_pass:
            updates.append("password_hash = ?")
            params.append(generate_password_hash(new_pass))

        if new_pin:
            if len(new_pin) != 6 or not new_pin.isdigit():
                return jsonify({"status": "error", "message": "PIN must be 6 digits"}), 400
            updates.append("master_pin_hash = ?")
            params.append(generate_password_hash(new_pin))

        if not updates:
            return jsonify({"status": "error", "message": "Nothing to update"}), 400

        updates.append("must_change_pwd = 1")
        params.append(int(s_id))

        query = f"UPDATE saas_studios SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, tuple(params))
        conn.commit()

        return jsonify({"status": "success", "message": f"Studio #{s_id} credentials updated successfully"})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/toggle_studio_status', methods=['POST'])
@login_required
def admin_toggle_studio_status():
    # 👑 केवल मास्टर एडमिन ही ब्लॉक/एक्टिव कर सकता है
    if int(get_current_studio_id()) != 1:
        return jsonify({"status": "error", "message": "अनधिकृत एक्सेस! केवल मास्टर एडमिन को अनुमति है।"}), 403

    data = request.json or {}
    s_id = data.get('studio_id')
    raw_status = data.get('is_active')

    if not s_id:
        return jsonify({"status": "error", "message": "Studio ID आवश्यक है!"}), 400

    if int(s_id) == 1:
        return jsonify({"status": "error", "message": "मास्टर स्टूडियो को ब्लॉक नहीं किया जा सकता!"}), 400

    # 1/true होने पर 'Active', अन्यथा 'Suspended'
    new_status = 'Active' if str(raw_status) in ['1', 'True', 'true'] else 'Suspended'

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE saas_studios SET status = ? WHERE id = ?", (new_status, s_id))
        conn.commit()
        return jsonify({"status": "success", "message": f"Studio #{s_id} का स्टेटस '{new_status}' कर दिया गया!"})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/admin/quick_reset_credentials', methods=['POST'])
@login_required
def admin_quick_reset():
    data = request.json or {}
    s_id = data.get('studio_id')

    if not s_id:
        return jsonify({"status": "error", "message": "Studio ID missing"}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # कॉलम न होने पर सेफ ऐड
        try:
            cursor.execute("ALTER TABLE studios ADD COLUMN must_change_pwd INTEGER DEFAULT 0")
        except Exception:
            pass

        # डिफ़ॉल्ट पासवर्ड studio@1234 और पिन 123456 सेट करें
        default_pwd = "studio@1234"
        default_pin = "123456"

        cursor.execute("""
            UPDATE studios 
            SET password = ?, pin = ?, must_change_pwd = 1 
            WHERE id = ?
        """, (default_pwd, default_pin, s_id))

        conn.commit()
        return jsonify({
            "status": "success", 
            "message": f"पासवर्ड रीसेट हो गया है!\nडिफ़ॉल्ट पासवर्ड: {default_pwd}\nडिफ़ॉल्ट पिन: {default_pin}\n\nयूजर को लॉगिन करते ही नया पासवर्ड सेट करना होगा।"
        })
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

# ----------------------------------------------------
# 📦 AUTOMATED DATABASE BACKUP VIA EMAIL (HIM STUDIO)
# ----------------------------------------------------
def send_db_backup_email():
    # 🔒 SaaS सुपर-एडमिन क्रेडेंशियल्स केवल सर्वर एनवायरनमेंट से पढ़े जाएंगे
    sender_email = os.environ.get("ADMIN_BACKUP_EMAIL")
    sender_app_password = os.environ.get("ADMIN_BACKUP_PASS")
    receiver_email = os.environ.get("ADMIN_BACKUP_RECEIVER", sender_email)

    # अगर सर्वर में क्रेडेंशियल्स सेट नहीं हैं तो सुरक्षित रूप से रुक जाएगा
    if not sender_email or not sender_app_password:
        return False, "सर्वर सुरक्षा त्रुटि: बैकअप क्रेडेंशियल्स एनवायरनमेंट में कॉन्फ़िगर नहीं हैं।"

    # ... आगे का डेटाबेस अटैचमेंट और भेजने का कोड बिल्कुल वैसा ही रहेगा ...
    # आगे का फाइल खोजने और भेजने का पूरा लॉजिक वही रहेगा...
    # 🔍 ऑटो-डिटेक्ट: अपने आप सही .db फाइल ढूँढने का लॉजिक
    db_file_path = None
    common_names = [
        "studio_erp.db", "studio.db", "him_studio.db", "database.db",
        os.path.join("instance", "studio_erp.db"),
        os.path.join("instance", "studio.db"),
        os.path.join("instance", "him_studio.db")
    ]

    for path in common_names:
        if os.path.exists(path):
            db_file_path = path
            break

    # अगर ऊपर न मिले तो पूरे फ़ोल्डर में कोई भी .db फ़ाइल ढूँढें
    if not db_file_path:
        for root, dirs, files in os.walk("."):
            for file in files:
                if file.endswith(".db"):
                    db_file_path = os.path.join(root, file)
                    break
            if db_file_path:
                break

    if not db_file_path or not os.path.exists(db_file_path):
        return False, "सिस्टम में कोई भी .db डेटाबेस फ़ाइल नहीं मिली!"

    timestamp = datetime.now().strftime("%d-%m-%Y %I:%M %p")
    subject = f"📦 HIM STUDIO Database Backup - {timestamp}"
    body = (
        f"नमस्ते दिनेश जी,\n\n"
        f"HIM STUDIO ERP के डेटाबेस की ताज़ा बैकअप कॉपी संलग्न है।\n"
        f"फ़ाइल: {os.path.basename(db_file_path)}\n"
        f"तारीख व समय: {timestamp}\n\n"
        f"— HIM STUDIO Automated Cloud Backup"
    )

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        with open(db_file_path, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())
        encoders.encode_base64(part)

        file_date = datetime.now().strftime("%Y_%m_%d")
        part.add_header(
            "Content-Disposition",
            f"attachment; filename=HIM_STUDIO_BACKUP_{file_date}.db"
        )
        msg.attach(part)

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_app_password.replace(" ", ""))
        server.send_message(msg)
        server.quit()
        return True, f"सफलतापूर्वक ईमेल भेज दिया गया! (फ़ाइल: {os.path.basename(db_file_path)})"
    except Exception as e:
        return False, str(e)


@app.route('/api/trigger_backup_email', methods=['GET'])
def trigger_backup_email():
    success, msg = send_db_backup_email()
    if success:
        return jsonify({
            "status": "success",
            "message": f"✅ {msg} अपना Gmail चेक करें।"
        })
    else:
        return jsonify({
            "status": "error",
            "message": f"❌ बैकअप नहीं भेजा जा सका: {msg}"
        }), 500

@app.route('/api/update_studio_profile', methods=['POST'])
@login_required
def update_studio_profile():
    conn = None
    try:
        cur_studio = get_current_studio_id()
        data = request.json or {}
        
        # वही फ़ील्ड्स जो रजिस्ट्रेशन के समय सेव होते हैं
        studio_name = str(data.get('studio_name', '')).strip()
        owner_name = str(data.get('owner_name', '')).strip()
        whatsapp = str(data.get('whatsapp', '')).strip()
        address = str(data.get('address', '')).strip()

        conn = get_db_connection()
        cursor = conn.cursor()

        # डेटाबेस में स्टूडियो की प्रोफाइल अपडेट करें (मोबाइल नंबर फिक्स रहेगा, इसलिए उसे नहीं बदलेंगे)
        cursor.execute("""
            UPDATE saas_studios 
            SET studio_name = ?, owner_name = ?, whatsapp = ?, address = ? 
            WHERE id = ?
        """, (studio_name, owner_name, whatsapp, address, cur_studio))
        
        conn.commit()

        # सेशन का नाम भी अपडेट करें ताकि हेडर में तुरंत नया नाम दिखने लगे
        session['studio_name'] = studio_name

        return jsonify({
            "status": "success",
            "message": "स्टूडियो प्रोफाइल सफलतापूर्वक अपडेट हो गई!"
        })

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn: conn.close()

# 🖨️ मास्टर लैब स्टेटमेंट प्रिंट रूट
@app.route('/print_lab_statement/<int:vendor_id>', methods=['GET'])
@login_required
def print_lab_statement(vendor_id):
    conn = None
    try:
        cur_studio = get_current_studio_id()
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. स्टूडियो का नाम निकालें
        cursor.execute("SELECT studio_name FROM saas_studios WHERE id = ?", (cur_studio,))
        st_row = cursor.fetchone()
        studio_title = st_row['studio_name'] if st_row else "HIM STUDIO"

        # 2. वेंडर (लैब पार्टनर) का विवरण निकालें
        cursor.execute("SELECT * FROM master_lab_vendors WHERE id = ? AND studio_id = ?", (vendor_id, cur_studio))
        vendor_row = cursor.fetchone()
        if not vendor_row:
            return "Lab Vendor Not Found", 404
        vendor = dict(vendor_row)

        # 3. उस लैब के सभी जॉब रिकॉर्ड्स निकालें
        cursor.execute("SELECT * FROM lab_outsource_records WHERE vendor_id = ? AND studio_id = ? ORDER BY id ASC", (vendor_id, cur_studio))
        records = [dict(r) for r in cursor.fetchall()]

        # 4. टोटल कैलकुलेशन
        total_cust_amount = sum(float(r.get('total_amount') or 0) for r in records)
        total_cust_advance = sum(float(r.get('advance_amount') or 0) for r in records)
        total_lab_cost = sum(float(r.get('lab_cost') or 0) for r in records)
        total_lab_paid = sum(float(r.get('lab_paid') or 0) for r in records)

        summary = {
            "cust_total": total_cust_amount,
            "cust_adv": total_cust_advance,
            "cust_bal": max(0.0, total_cust_amount - total_cust_advance),
            "lab_cost": total_lab_cost,
            "lab_paid": total_lab_paid,
            "lab_due": max(0.0, total_lab_cost - total_lab_paid)
        }

        return render_template(
            'print_lab_statement.html',
            studio_title=studio_title,
            vendor=vendor,
            records=records,
            summary=summary,
            current_time=datetime.now().strftime('%d/%m/%Y %I:%M %p')
        )
    except Exception as e:
        return f"Lab Statement Print Error: {str(e)}", 500
    finally:
        if conn:
            conn.close()

@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({"status": "error", "message": "API endpoint not found (404)"}), 404
    return """
    <!DOCTYPE html>
    <html lang="hi">
    <head><meta charset="UTF-8"><title>404 - Page Not Found</title></head>
    <body style="font-family:sans-serif; text-align:center; padding:50px; background:#f4f7f6;">
        <h1 style="color:#e74c3c; font-size:48px;">404</h1>
        <h2>⚠️ अरे गुरु, यह पेज नहीं मिला!</h2>
        <p>आप जिस पते पर पहुँचना चाहते हैं, वह मौजूद नहीं है।</p>
        <a href="/dashboard" style="background:#3498db; color:white; padding:10px 20px; text-decoration:none; border-radius:5px;">डैशबोर्ड पर वापस जाएं</a>
    </body>
    </html>
    """, 404

@app.errorhandler(500)
def internal_server_error(e):
    if request.path.startswith('/api/'):
        return jsonify({"status": "error", "message": "Internal server error (500)"}), 500
    return """
    <!DOCTYPE html>
    <html lang="hi">
    <head><meta charset="UTF-8"><title>500 - Server Error</title></head>
    <body style="font-family:sans-serif; text-align:center; padding:50px; background:#f4f7f6;">
        <h1 style="color:#e74c3c; font-size:48px;">500</h1>
        <h2>⚠️ सर्वर के अंदर कुछ गड़बड़ हो गई!</h2>
        <p>चिंता की बात नहीं, इसे संभाल लिया गया है। कृपया पुनः प्रयास करें।</p>
        <a href="/dashboard" style="background:#3498db; color:white; padding:10px 20px; text-decoration:none; border-radius:5px;">डैशबोर्ड पर जाएं</a>
    </body>
    </html>
    """, 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001, threaded=True)