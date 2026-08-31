from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
import os
import json
import re
import base64

APP_VERSION = 'v1.0.5'
import io
import logging
from collections import defaultdict, deque
from datetime import timedelta, datetime, date, timezone
from functools import wraps
import time
import secrets
from dotenv import load_dotenv
from supabase import create_client, Client
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from PIL import Image
except ImportError:  # Pillow may not be installed in every environment
    Image = None

try:
    import sys
    import io as _io
    _weasy_err = _io.StringIO()
    _orig_stderr = sys.stderr
    sys.stderr = _weasy_err
    from weasyprint import HTML
    sys.stderr = _orig_stderr
except (ImportError, OSError):
    sys.stderr = _orig_stderr
    HTML = None
finally:
    sys.stderr = _orig_stderr

# Fallback PDF engine (pure Python, no native deps)
try:
    from xhtml2pdf import pisa as _pisa
except ImportError:
    _pisa = None

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

# ============================================================
# Logging Setup
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('vgrand')

# ============================================================
# Security: Secret Key
# ============================================================
_secret = os.environ.get('SECRET_KEY')
if not _secret:
    if os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == '1':
        _secret = 'dev-only-secret-key-DO-NOT-USE-IN-PROD'
        logger.warning('SECRET_KEY not set — using insecure dev-only key. DO NOT use in production.')
    else:
        raise RuntimeError('SECRET_KEY environment variable is not set. Refusing to start.')

def get_client_ip():
    """Get real client IP, accounting for reverse proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'

def render_pdf(html_string):
    """Render HTML to PDF using WeasyPrint, with xhtml2pdf fallback."""
    if HTML:
        return HTML(string=html_string).write_pdf()
    if _pisa:
        import io as _io2
        buf = _io2.BytesIO()
        _pisa.CreatePDF(_io2.StringIO(html_string), dest=buf)
        return buf.getvalue()
    raise RuntimeError('No PDF engine available. Install weasyprint or xhtml2pdf.')

def now_ist():
    return datetime.now(IST)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'), template_folder=os.path.join(BASE_DIR, 'templates'))
app.secret_key = _secret
app.permanent_session_lifetime = timedelta(days=30)

# Add gzip compression and cache-friendly headers for static files
class GzipMiddleware:
    def __init__(self, app, minimum_size=500):
        self.app = app
        self.minimum_size = minimum_size

    def __call__(self, environ, start_response):
        if 'gzip' not in environ.get('HTTP_ACCEPT_ENCODING', ''):
            return self.app(environ, start_response)

        # Buffer the response to check size before deciding to gzip
        captured = {}
        def capture_start_response(status, headers, exc_info=None):
            captured['status'] = status
            captured['headers'] = headers
            return lambda data: None

        body = b''.join(self.app(environ, capture_start_response))
        status = captured.get('status', '200 OK')
        headers = captured.get('headers', [])

        if not status.startswith('200') or len(body) < self.minimum_size:
            start_response(status, headers)
            return [body]

        import gzip
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb') as f:
            f.write(body)
        compressed = buf.getvalue()

        if len(compressed) >= len(body):
            start_response(status, headers)
            return [body]

        headers = [(k, v) for k, v in headers if k.lower() not in ('content-length', 'content-encoding')]
        headers.append(('Content-Encoding', 'gzip'))
        headers.append(('Content-Length', str(len(compressed))))
        start_response(status, headers)
        return [compressed]

app.wsgi_app = GzipMiddleware(app.wsgi_app)

@app.after_request
def set_cache_headers(response):
    """Set cache headers: static files get long cache, HTML gets revalidate."""
    if request.path.startswith('/static/'):
        # Static files with ?v= query params — cache for 1 year
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        response.headers['Vary'] = 'Accept-Encoding'
    elif response.content_type and 'text/html' in response.content_type:
        # HTML pages — revalidate to get latest version but allow 304
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
    return response

@app.route('/static/<path:filename>')
def static_files(filename):
    """Serve static files with long cache headers."""
    cache_timeout = 31536000  # 1 year
    response = send_from_directory(app.static_folder, filename)
    response.headers['Cache-Control'] = f'public, max-age={cache_timeout}, immutable'
    response.headers['Vary'] = 'Accept-Encoding'
    return response

@app.route('/robots.txt')
def robots_txt():
    """Serve robots.txt for search engine crawlers."""
    response = send_from_directory(app.static_folder, 'robots.txt', mimetype='text/plain')
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

@app.route('/sitemap.xml')
def sitemap_xml():
    """Serve sitemap.xml for search engine crawlers."""
    response = send_from_directory(app.static_folder, 'sitemap.xml', mimetype='application/xml')
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

app.config.update(
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get('FLASK_ENV') != 'development' and os.environ.get('DEV_MODE') != '1',
)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

@app.template_filter('inr')
def format_inr_filter(num):
    """Jinja filter: format a number as Indian Rupees (Cr/Lakh)."""
    return _format_inr(num)

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY', '')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')

# Use the service-role key server-side so RLS policies can be deny-by-default.
# The browser never touches Supabase directly, so this key never leaves the server.
_supabase_key = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY
supabase: Client = create_client(SUPABASE_URL, _supabase_key) if SUPABASE_URL and _supabase_key else None

if supabase:
    logger.info(f'Supabase connected: {SUPABASE_URL}')
else:
    logger.warning('Supabase not connected. Check SUPABASE_URL and SUPABASE_SERVICE_KEY in .env')

# ============================================================
# Auto-migration: run pending SQL migrations on startup
# Uses DATABASE_URL (direct Postgres connection) if available.
# This ensures tables like 'attendance' and 'user_prefs' exist
# without requiring manual SQL Editor execution.
# ============================================================
DATABASE_URL = os.environ.get('DATABASE_URL', '')

def _run_migrations():
    """Run all pending migration SQL files in order."""
    if not DATABASE_URL:
        logger.info('No DATABASE_URL set - skipping auto-migration. '
                    'Run migrations manually in Supabase SQL Editor.')
        return
    if psycopg2 is None:
        logger.warning('psycopg2 not installed - skipping auto-migration.')
        return

    migrations_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migrations')
    if not os.path.isdir(migrations_dir):
        logger.warning(f'Migrations directory not found: {migrations_dir}')
        return

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()

        # Ensure schema_migrations tracking table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS _schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT now()
            )
        """)

        # Get already-applied migrations
        cur.execute("SELECT filename FROM _schema_migrations")
        applied = {row[0] for row in cur.fetchall()}

        # Run pending migrations in sorted order
        sql_files = sorted(f for f in os.listdir(migrations_dir) if f.endswith('.sql'))
        for fname in sql_files:
            if fname in applied:
                continue
            fpath = os.path.join(migrations_dir, fname)
            logger.info(f'Applying migration: {fname}')
            with open(fpath, 'r', encoding='utf-8') as f:
                sql_content = f.read()
            try:
                cur.execute(sql_content)
                cur.execute(
                    "INSERT INTO _schema_migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING",
                    (fname,)
                )
                logger.info(f'Migration applied: {fname}')
            except Exception as e:
                logger.error(f'Migration failed {fname}: {e}')
                # Continue with next migration - some may depend on later ones

        logger.info('Auto-migration complete.')
    except Exception as e:
        logger.error(f'Auto-migration error: {e}')
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass

_run_migrations()

# --- Pollinations AI (Feature 1: interior design) ---
POLLINATIONS_API_TOKEN = os.environ.get('POLLINATIONS_API_TOKEN', '')


# ============================================================
# CSRF: Origin Validation for state-changing requests
# ============================================================
@app.before_request
def _csrf_origin_check():
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        origin = request.headers.get('Origin') or request.headers.get('Referer') or ''
        if not origin:
            return None
        host = request.host_url.rstrip('/')
        # Direct match
        if origin.startswith(host):
            return None
        # Normalize localhost / 127.0.0.1 / 0.0.0.0 equivalence
        from urllib.parse import urlparse
        origin_parts = urlparse(origin)
        host_parts = urlparse(host)
        localhost_aliases = {'localhost', '127.0.0.1', '0.0.0.0'}
        if (origin_parts.hostname in localhost_aliases and
                host_parts.hostname in localhost_aliases and
                origin_parts.port == host_parts.port):
            return None
        logger.warning(f'CSRF: Origin mismatch — origin={origin}, host={host}, path={request.path}')
        return jsonify({'error': 'Cross-origin request blocked'}), 403
    return None


def load_json_fallback(filename):
    try:
        path = os.path.join(os.path.dirname(__file__), 'live_data', filename)
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f'Error loading JSON fallback {filename}: {e}')
        return None

DEFAULT_WORK_ITEMS = [
    "BRICK WORK", "ELECTRICAL PIPES", "MESH", "PLASTERING",
    "CEILING PAINT", "POP FRAME", "CEILING WIRING", "POP SHEETS",
    "WALL CARE", "BATHROOM PLUMBING", "WINDOW FRAME", "BATH SWR LINES",
    "BATH CONCEALING", "TILES", "DOORS FITTING", "PAINT PRIMER",
    "PAINT 1st COAT", "WINDOWS PAINT", "SWITCH BOARD FITTING",
    "PATCH WORK", "2nd COAT PAINTING"
]

FLOORS = ["1st Floor", "2nd Floor", "3rd Floor", "4th Floor", "5th Floor"]
FLATS_PER_FLOOR = 6


_active_cache = {}  # {user_id: (is_active, timestamp)}

def _is_user_active(user_id):
    now = time.time()
    cached = _active_cache.get(user_id)
    if cached and (now - cached[1]) < 60:
        return cached[0]
    if not supabase:
        return True
    try:
        res = supabase.table('users').select('active').eq('id', user_id).execute()
        is_active = bool(res.data and res.data[0].get('active', False))
    except Exception:
        is_active = True
    _active_cache[user_id] = (is_active, now)
    return is_active


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login_page'))
        user = session['user']
        if not _is_user_active(user.get('id')):
            session.pop('user', None)
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


def requires_role(*allowed_roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            user = session.get('user')
            role = user.get('role') if isinstance(user, dict) else 'admin'
            if role not in allowed_roles:
                return jsonify({'error': 'Forbidden'}), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator


def requires_role_or_override(*primary_roles):
    """Like requires_role, but admin and manager always have override access."""
    all_roles = set(primary_roles) | {'admin', 'manager'}
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            user = session.get('user')
            role = user.get('role') if isinstance(user, dict) else 'admin'
            if role not in all_roles:
                return jsonify({'error': 'Forbidden'}), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator


def _allowed_ventures(user):
    if not supabase:
        return set()
    # Admin, manager, and supervisor roles see all ventures in their org by default.
    # Role-based access controls restrict *actions*, not legitimate data visibility.
    if user.get('role') in ('admin', 'manager', 'supervisor'):
        org_id = user.get('org_id')
        # Include ventures in this org PLUS legacy ventures with NULL org_id
        res = supabase.table('ventures').select('id').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
        return {r['id'] for r in (res.data or [])}
    # Other roles (e.g. custom restricted roles) use explicit user_ventures grants.
    rows = supabase.table('user_ventures').select('venture_id').eq('user_id', user['id']).execute()
    allowed = {r['venture_id'] for r in (rows.data or [])}
    if not allowed:
        org_id = user.get('org_id')
        res = supabase.table('ventures').select('id').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
        allowed = {r['id'] for r in (res.data or [])}
    return allowed


def _verify_same_org(target_user_id):
    if not supabase:
        return True
    admin_org = session['user'].get('org_id')
    try:
        res = supabase.table('users').select('org_id').eq('id', target_user_id).execute()
        if not res.data or res.data[0].get('org_id') != admin_org:
            return False
    except Exception:
        return False
    return True


def compress_image_data_url(data_url, max_size=(1024, 1024), quality=65):
    if Image is None:
        return data_url
    m = re.match(r'^data:image/(jpeg|png|webp);base64,(.*)$', data_url, re.IGNORECASE)
    if not m:
        return data_url
    try:
        raw = base64.b64decode(m.group(2), validate=True)
        img = Image.open(io.BytesIO(raw))
        if img.mode in ('RGBA', 'P'):
            rgb = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            if img.mode == 'RGBA':
                rgb.paste(img, mask=img.split()[3])
            else:
                rgb.paste(img)
            img = rgb
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=quality, optimize=True)
        out.seek(0)
        encoded = base64.b64encode(out.read()).decode('ascii')
        return f'data:image/jpeg;base64,{encoded}'
    except Exception as e:
        app.logger.warning(f'Image compression failed: {e}')
        return data_url


def compress_images_in_data(data):
    if isinstance(data, dict):
        return {k: compress_images_in_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [compress_images_in_data(v) for v in data]
    if isinstance(data, str) and data.startswith('data:image'):
        return compress_image_data_url(data)
    return data


def get_or_create_storage_bucket(bucket_name):
    """Ensure a Supabase Storage bucket exists. Returns True on success."""
    if not supabase:
        return False
    try:
        buckets = supabase.storage.list_buckets()
        if any(b.get('name') == bucket_name or b.name == bucket_name for b in buckets):
            return True
        supabase.storage.create_bucket(bucket_name, bucket_name, {'public': True})
        return True
    except Exception as e:
        app.logger.warning(f'Bucket check/create failed for {bucket_name}: {e}')
        return False


def upload_bytes_to_storage(bucket_name, path, data, content_type='application/octet-stream'):
    """Upload bytes to Supabase Storage and return the public URL, or (None, error) on failure."""
    if not supabase:
        return None, 'Supabase not connected'
    try:
        bucket_ok = get_or_create_storage_bucket(bucket_name)
        if not bucket_ok:
            # Bucket may already exist but list/create failed; still try upload.
            pass
        supabase.storage.from_(bucket_name).upload(path, data, {'content-type': content_type})
        url = supabase.storage.from_(bucket_name).get_public_url(path)
        return url, None
    except Exception as e:
        err = str(e)
        app.logger.warning(f'Upload to {bucket_name}/{path} failed: {err}')
        return None, err


def _get_public_image_url(bucket_name, path):
    """Return Supabase public URL for a storage path."""
    if not supabase:
        return None
    try:
        return supabase.storage.from_(bucket_name).get_public_url(path)
    except Exception as e:
        app.logger.warning(f'Public URL failed for {bucket_name}/{path}: {e}')
        return None


def _get_signed_image_url(bucket_name, path, expires_in=3600):
    """Return a signed URL for a private storage object."""
    if not supabase:
        return None
    try:
        res = supabase.storage.from_(bucket_name).create_signed_url(path, expires_in)
        return res.get('signedURL') if isinstance(res, dict) else res
    except Exception as e:
        app.logger.warning(f'Signed URL failed for {bucket_name}/{path}: {e}')
        return None


def _is_url_accessible(url, timeout=10):
    """Check that a URL is reachable over HTTP(S). Pollinations needs this."""
    if not url or url.startswith('data:'):
        return False
    import requests
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code < 400:
            return True
    except Exception:
        pass
    try:
        r = requests.get(url, timeout=timeout, stream=True)
        r.close()
        return r.status_code < 400
    except Exception:
        return False
    return False


def get_fetchable_image_url(bucket_name, path, expiry=3600):
    """Return a URL Pollinations can fetch: public URL if reachable, else signed URL."""
    public_url = _get_public_image_url(bucket_name, path)
    if public_url and _is_url_accessible(public_url):
        return public_url
    signed_url = _get_signed_image_url(bucket_name, path, expiry)
    if signed_url and _is_url_accessible(signed_url):
        return signed_url
    return None


def enhance_design_prompt(room_type, style, budget_tier, area_sqft=120):
    """
    Uses GLM (via Pollinations text API) to turn simple selections into a rich
    kontext editing instruction. Falls back to a detailed template if GLM fails —
    never let a text-generation hiccup block image generation.
    """
    import requests

    budget_materials = {
        'economy': {
            'Living Room': 'laminate flooring, budget fabric sofa, simple TV unit, basic curtains',
            'Bedroom': 'vinyl flooring, engineered-wood bed frame, budget wardrobe, simple bedding',
            'Kitchen': 'laminate cabinets, granite-look countertop, basic chimney, SS sink',
            'Bathroom': 'ceramic wall tiles, PVC vanity, budget sanitaryware, simple mirror',
            'Dining Room': 'engineered wood dining table, basic upholstered chairs, simple pendant light',
            'Home Office': 'laminate desk, basic ergonomic chair, open shelves, task lamp',
            'Balcony': 'outdoor tiles, plastic/wooden planters, basic outdoor seating',
        },
        'mid-range': {
            'Living Room': 'engineered wood flooring, sectional sofa, built-in TV unit, designer curtains',
            'Bedroom': 'engineered wood flooring, upholstered bed, modular wardrobe, premium bedding',
            'Kitchen': 'acrylic cabinets, quartz countertop, branded chimney, SS appliances',
            'Bathroom': 'vitrified wall tiles, ceramic vanity, branded sanitaryware, LED mirror',
            'Dining Room': 'solid wood dining table, upholstered chairs, modern chandelier',
            'Home Office': 'wooden desk, ergonomic chair, closed cabinets, ambient lighting',
            'Balcony': 'wooden deck tiles, metal planters, weather-resistant lounge seating',
        },
        'premium': {
            'Living Room': 'Italian marble flooring, designer leather sofa, custom TV wall, smart lighting',
            'Bedroom': 'Italian marble flooring, luxury upholstered bed, walk-in wardrobe, silk bedding',
            'Kitchen': 'high-gloss modular cabinets, quartzite countertop, built-in oven, chimney hob',
            'Bathroom': 'imported marble tiles, designer vanity, premium sanitaryware, rainfall shower',
            'Dining Room': 'imported marble flooring, designer dining set, statement chandelier, artwork',
            'Home Office': 'executive wooden desk, leather chair, custom library, designer lighting',
            'Balcony': 'premium deck tiles, designer planters, outdoor sofa set, ambient lights',
        },
    }
    materials = budget_materials.get(budget_tier.lower(), budget_materials['mid-range']).get(room_type, 'mid-range furnishings')

    style_directive = {
        'Modern': 'clean lines, minimal ornamentation, neutral palette with bold accents',
        'Minimalist': 'very sparse, white and wood tones, hidden storage, no clutter',
        'Traditional': 'classic carved wood, warm colors, ornate details, rich textiles',
        'Luxury': 'rich materials, gold/brass accents, plush textures, statement pieces',
        'Industrial': 'exposed brick/metal, Edison bulbs, raw wood, loft aesthetic',
        'Scandinavian': 'light wood, white walls, cozy textiles, functional furniture',
        'Contemporary': 'mixed textures, curved forms, muted colors, art-forward',
    }.get(style, 'modern interior design')

    fallback = (
        f"interior design renovation of the same {room_type} photograph, approximately {area_sqft} sqft, "
        f"preserve the exact camera angle, room proportions, wall positions, window placements, and ceiling height, "
        f"apply a {style} look with {style_directive}, "
        f"use {materials} suitable for a {budget_tier} budget, "
        f"photorealistic 3D render, consistent daylight, same viewpoint"
    )

    if not POLLINATIONS_API_TOKEN:
        return fallback

    system_prompt = (
        "You write short, specific image-editing instructions for an AI interior design tool. "
        "Output ONE paragraph, under 70 words, no preamble, no markdown. "
        "Describe concrete materials, furniture, and finishes. "
        "Emphasize preserving the exact camera angle, room shape, walls, windows, and layout."
    )
    user_prompt = f"Room type: {room_type} ({area_sqft} sqft). Style: {style}. Budget level: {budget_tier}. Materials: {materials}."

    try:
        resp = requests.post(
            "https://gen.pollinations.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {POLLINATIONS_API_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "model": "glm",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 120,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip()
            if text:
                return text
        return fallback
    except Exception:
        return fallback


def generate_room_design(image_url, prompt, seed=0):
    """Calls Pollinations' image-to-image API to redesign a room photo.
    Tries the paid gen.pollinations.ai endpoint first, then falls back to
    the free legacy image.pollinations.ai endpoint."""
    import requests
    from urllib.parse import quote
    from time import sleep

    encoded_prompt = quote(prompt)
    base_params = {
        "model": "flux",
        "image": image_url,
        "width": 1024,
        "height": 1024,
        "seed": seed,
        "negative": "changed room layout, moved walls, removed windows, added windows, different camera angle, different perspective, altered room shape, different ceiling, exterior view",
    }

    endpoints = []
    if POLLINATIONS_API_TOKEN:
        endpoints.append(("https://gen.pollinations.ai/image/", {"Authorization": f"Bearer {POLLINATIONS_API_TOKEN}"}, {**base_params, "nologo": "true"}))
    endpoints.append(("https://image.pollinations.ai/prompt/", {}, dict(base_params)))

    last_error = "Unknown error"
    for ep_url, ep_headers, ep_params in endpoints:
        url = f"{ep_url}{encoded_prompt}"
        for attempt in range(3):
            try:
                resp = requests.get(url, params=ep_params, headers=ep_headers, timeout=120)
                content_type = resp.headers.get('content-type', '')
                if resp.status_code == 200 and 'image' in content_type:
                    return True, resp.content
                last_error = f"Pollinations error {resp.status_code} ({content_type}): {resp.text[:200]}"
                if resp.status_code in (401, 402, 403):
                    break
            except requests.exceptions.RequestException as e:
                last_error = f"Request failed: {e}"
            if attempt < 2:
                sleep(2 ** attempt)
    return False, last_error


def compute_design_cost_estimate(room_type, budget_tier, area_sqft=120):
    """Return a cost estimate dict scaled to the provided area in sqft."""
    try:
        area = float(area_sqft)
        if area <= 0:
            area = 120
    except (TypeError, ValueError):
        area = 120

    if supabase:
        try:
            res = supabase.table('design_cost_rates').select('*').eq(
                'room_type', room_type).eq('budget_tier', budget_tier).limit(1).execute()
            if res.data:
                row = res.data[0]
                return {
                    'room_type': room_type,
                    'budget_tier': budget_tier,
                    'area_sqft': area,
                    'material_rate_per_sqft': float(row['material_rate_per_sqft']),
                    'labor_rate_per_sqft': float(row['labor_rate_per_sqft']),
                    'sample_area_sqft': area,
                    'material_cost': round(float(row['material_rate_per_sqft']) * area, 2),
                    'labor_cost': round(float(row['labor_rate_per_sqft']) * area, 2),
                    'total_estimate': round((float(row['material_rate_per_sqft']) + float(row['labor_rate_per_sqft'])) * area, 2),
                    'currency': 'INR'
                }
        except Exception as e:
            app.logger.warning(f'Cost rate lookup failed: {e}')

    defaults = {
        'economy': (250, 150),
        'mid-range': (450, 250),
        'premium': (900, 500),
    }
    material, labor = defaults.get(budget_tier.lower(), (450, 250))
    return {
        'room_type': room_type,
        'budget_tier': budget_tier,
        'area_sqft': area,
        'material_rate_per_sqft': material,
        'labor_rate_per_sqft': labor,
        'sample_area_sqft': area,
        'material_cost': round(material * area, 2),
        'labor_cost': round(labor * area, 2),
        'total_estimate': round((material + labor) * area, 2),
        'currency': 'INR',
        'note': 'fallback estimate'
    }


# --- Marketplace seed data (verified July 2026) ---
MARKETPLACE_SEED_DATA = [
    {
        "category": "Structural", "material": "OPC 53 Grade Cement", "unit": "50kg bag",
        "suppliers": [
            {"company_name": "UltraTech Cement (Aditya Birla Group)", "brand_name": "UltraTech",
             "price_low": 340, "price_high": 465, "trust_level": "Verified — market leader",
             "email": "ultratech.communication@adityabirla.com", "phone": "1800 210 3311",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Toll-free + email confirmed via ultratechcement.com"},
            {"company_name": "ACC Limited (Adani Group)", "brand_name": "ACC",
             "price_low": 370, "price_high": 470, "trust_level": "Verified",
             "email": "", "phone": "1800 1033 444",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Toll-free confirmed via acclimited.com; no public direct email found"},
            {"company_name": "Ambuja Cements Ltd (Adani Group)", "brand_name": "Ambuja",
             "price_low": 360, "price_high": 435, "trust_level": "Verified",
             "email": "corporate.communications@ambujacement.com", "phone": "1800 22 3010",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Confirmed via ambujacement.com contact page"},
            {"company_name": "Shree Cement Ltd", "brand_name": "Shree Cement",
             "price_low": 320, "price_high": 370, "trust_level": "Verified — value pick",
             "email": "", "phone": "1800 180 6003",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Toll-free confirmed; no public direct email found"},
            {"company_name": "Dalmia Cement (Bharat) Ltd", "brand_name": "Dalmia Cement",
             "price_low": 290, "price_high": 420, "trust_level": "Verified — competitive bulk pricing",
             "email": "marketing@dalmiacement.com", "phone": "011-23310121",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Confirmed via dalmiacement.com"},
        ],
    },
    {
        "category": "Structural", "material": "TMT Steel Bars (Fe 500/500D/550D)", "unit": "per kg",
        "suppliers": [
            {"company_name": "Tata Steel Ltd (Tata Tiscon)", "brand_name": "Tata Tiscon",
             "price_low": 57, "price_high": 78, "trust_level": "Verified — premium/widest network",
             "email": "sntitatasteel@conneqtcorp.com", "phone": "1800 108 8282",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Confirmed via tatatiscon.co.in"},
            {"company_name": "JSW Steel Ltd (JSW Neosteel)", "brand_name": "JSW Neosteel",
             "price_low": 61, "price_high": 78, "trust_level": "Verified — seismic-grade focus",
             "email": "", "phone": "",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Price range from dealer aggregators; direct contact pending — do not fabricate"},
            {"company_name": "Steel Authority of India Ltd (SAIL)", "brand_name": "SAIL TMT",
             "price_low": 59, "price_high": 75, "trust_level": "Verified — government-backed",
             "email": "", "phone": "",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Price range from dealer aggregators; direct contact pending — do not fabricate"},
            {"company_name": "Rashtriya Ispat Nigam Ltd (Vizag Steel)", "brand_name": "Vizag Steel",
             "price_low": 44, "price_high": 56, "trust_level": "Verified — regional value leader (South India)",
             "email": "", "phone": "",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Price range from Vizag-region dealer trackers"},
            {"company_name": "Jindal Steel & Power (Jindal Panther)", "brand_name": "Jindal Panther",
             "price_low": 59, "price_high": 75, "trust_level": "Verified — competitive mid-tier value",
             "email": "", "phone": "",
             "price_last_verified_at": "2026-07-10",
             "source_note": "Price range from dealer aggregators; direct contact pending — do not fabricate"},
        ],
    },
]


def run_marketplace_seed():
    """Idempotent: upserts on (material name, company_name), never duplicates rows,
    never overwrites manually-edited admin data."""
    if not supabase:
        return
    for entry in MARKETPLACE_SEED_DATA:
        existing = supabase.table('marketplace_materials').select('id').eq('name', entry['material']).execute()
        if existing.data:
            material_id = existing.data[0]['id']
        else:
            inserted = supabase.table('marketplace_materials').insert({
                'category': entry['category'], 'name': entry['material'], 'unit': entry['unit'],
            }).execute()
            material_id = inserted.data[0]['id'] if inserted.data else None
            if not material_id:
                continue

        for s in entry['suppliers']:
            existing_supplier = supabase.table('marketplace_suppliers').select('id').eq(
                'material_id', material_id).eq('company_name', s['company_name']).execute()
            if existing_supplier.data:
                continue  # don't overwrite — admin may have edited this row already
            supabase.table('marketplace_suppliers').insert({**s, 'material_id': material_id}).execute()


@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password are required'}), 400

    # Authenticate against the Supabase users table only.
    user_obj = None
    if supabase:
        try:
            res = supabase.table('users').select('*').ilike('email', username).eq('active', True).execute()
            if res.data:
                row = res.data[0]
                pw_hash = row.get('password_hash', '')
                if pw_hash and check_password_hash(pw_hash, password):
                    user_obj = {'id': row['id'], 'email': row['email'], 'role': row['role'], 'org_id': row.get('org_id')}
                else:
                    logger.warning(f'Login failed for "{username}": password mismatch')
            else:
                logger.warning(f'Login failed for "{username}": no active user found')
        except Exception as e:
            logger.error(f'Error loading user from Supabase: {e}')
    else:
        logger.warning('Login failed: Supabase not connected')

    if user_obj:
        session['user'] = user_obj
        session.permanent = True
        return jsonify({'success': True, 'user': user_obj['email'], 'role': user_obj['role']})
    return jsonify({'success': False, 'error': 'Invalid credentials'}), 401


@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    session.pop('visitor_user', None)
    session.pop('security_user', None)
    return jsonify({'success': True})


# ============================================================
# OTP Service Abstraction
# ============================================================

def send_otp(mobile, code):
    """Send OTP to a mobile number. Replace this with Twilio/MSG91/etc.
    For development, the code is simply logged."""
    logger.info(f'[OTP] Sending code {code} to {mobile}')
    return True


def generate_otp():
    """Generate a 4-digit OTP. In dev, always 1234."""
    return '1234'


# ============================================================
# Visitor Management API
# ============================================================

@app.route('/api/visitor/resident-login', methods=['POST'])
def visitor_resident_login():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    mobile = body.get('mobile', '').strip()
    if not mobile:
        return jsonify({'error': 'Mobile number required'}), 400
    try:
        res = supabase.table('residents').select('*').eq('mobile', mobile).eq('active', True).execute()
        if not res.data:
            return jsonify({'error': 'Resident not found'}), 404
        row = res.data[0]
        session['visitor_user'] = {
            'id': row['id'],
            'name': row['name'],
            'mobile': row['mobile'],
            'block': row['block'],
            'floor': row['floor'],
            'flat': row['flat'],
            'role': 'resident'
        }
        return jsonify({'success': True, 'resident': session['visitor_user']})
    except Exception as e:
        logger.error(f'Error resident login: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/visitor/security-login', methods=['POST'])
def visitor_security_login():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    email = body.get('email', '').strip()
    password = body.get('password', '')
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    try:
        res = supabase.table('security_users').select('*').ilike('email', email).eq('active', True).execute()
        if not res.data:
            return jsonify({'error': 'Invalid credentials'}), 401
        row = res.data[0]
        if not check_password_hash(row.get('password_hash', ''), password):
            return jsonify({'error': 'Invalid credentials'}), 401
        session['security_user'] = {
            'id': row['id'],
            'name': row['name'],
            'email': row['email'],
            'role': 'security'
        }
        return jsonify({'success': True, 'security': session['security_user']})
    except Exception as e:
        logger.error(f'Error security login: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/users')
@requires_role('admin')
def api_users():
    if not supabase:
        return jsonify([])
    try:
        org_id = session['user'].get('org_id')
        res = supabase.table('users').select('id, email, role, active, full_name').eq('org_id', org_id).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching users: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/change-password', methods=['POST'])
@requires_role('admin')
def api_users_change_password():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    data = request.get_json() or {}
    email = data.get('email', '').strip()
    new_password = data.get('new_password', '')
    if not email or not new_password:
        return jsonify({'error': 'Email and new password are required'}), 400
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    try:
        res = supabase.table('users').select('*').ilike('email', email).execute()
        if not res.data:
            return jsonify({'error': 'User not found'}), 404
        user = res.data[0]
        if not _verify_same_org(user['id']):
            return jsonify({'error': 'Forbidden: user belongs to a different organization'}), 403
        new_hash = generate_password_hash(new_password)
        supabase.table('users').update({'password_hash': new_hash}).eq('id', user['id']).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error changing password: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/visitor/me')
def visitor_me():
    return jsonify({
        'resident': session.get('visitor_user'),
        'security': session.get('security_user')
    })


@app.route('/api/visitor/resident')
def api_visitor_resident():
    resident = session.get('visitor_user')
    if not resident:
        return jsonify({'error': 'Not logged in'}), 401
    return jsonify(resident)


def visitor_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session and 'security_user' not in session and 'visitor_user' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


@app.route('/api/visitor/resident-profile', methods=['GET', 'PATCH'])
@visitor_login_required
def api_visitor_resident_profile():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    resident = session.get('visitor_user')
    if not resident:
        return jsonify({'error': 'Not logged in'}), 401
    if request.method == 'GET':
        try:
            res = supabase.table('residents').select('*').eq('id', resident['id']).single().execute()
            if not res.data:
                return jsonify({'error': 'Resident not found'}), 404
            r = res.data
            return jsonify({
                'id': r['id'], 'name': r['name'], 'mobile': r['mobile'],
                'email': r.get('email'), 'photo_url': r.get('photo_url'),
                'block': r['block'], 'floor': r['floor'], 'flat': r['flat'],
                'directory_opt_in': r.get('directory_opt_in', False),
                'active': r.get('active', True), 'created_at': r.get('created_at')
            })
        except Exception as e:
            logger.error(f'Error fetching resident profile: {e}')
            return jsonify({'error': str(e)}), 500
    else:
        body = request.get_json() or {}
        allowed = {k: v for k, v in body.items() if k in ('name', 'email', 'photo_url', 'directory_opt_in')}
        if not allowed:
            return jsonify({'error': 'Nothing to update'}), 400
        try:
            supabase.table('residents').update(allowed).eq('id', resident['id']).execute()
            if 'name' in allowed:
                resident['name'] = allowed['name']
                session['visitor_user'] = resident
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f'Error updating resident profile: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/visitor/resident-by-mobile/<mobile>')
@visitor_login_required
def api_visitor_resident_by_mobile(mobile):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        res = supabase.table('residents').select('*').eq('mobile', mobile).eq('active', True).execute()
        if not res.data:
            return jsonify(None)
        row = res.data[0]
        return jsonify({
            'id': row['id'],
            'name': row['name'],
            'mobile': row['mobile'],
            'block': row['block'],
            'floor': row['floor'],
            'flat': row['flat']
        })
    except Exception as e:
        logger.error(f'Error fetching resident by mobile: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/visitor/request', methods=['POST'])
@visitor_login_required
def api_visitor_request_create():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    required = ['resident_id', 'visitor_name']
    for field in required:
        if field not in body or not body[field]:
            return jsonify({'error': f'{field} is required'}), 400
    try:
        security = session.get('security_user') or session.get('user') or {}
        security_id = security.get('id') if security.get('role') == 'security' else None
        code = generate_otp()
        data = {
            'resident_id': body['resident_id'],
            'security_id': security_id,
            'visitor_name': body['visitor_name'],
            'visitor_mobile': body.get('visitor_mobile', ''),
            'purpose': body.get('purpose', ''),
            'visitor_count': int(body.get('visitor_count', 1) or 1),
            'vehicle_number': body.get('vehicle_number', ''),
            'id_proof_type': body.get('id_proof_type', ''),
            'remarks': body.get('remarks', ''),
            'status': 'waiting',
            'otp_code': code,
            'entry_time': now_ist().isoformat()
        }
        res = supabase.table('visitor_requests').insert(data).execute()
        if not res.data:
            return jsonify({'error': 'Failed to create visitor request'}), 500
        visitor_id = res.data[0]['id']

        # Get resident mobile to send OTP
        resident_res = supabase.table('residents').select('mobile').eq('id', body['resident_id']).execute()
        mobile = resident_res.data[0]['mobile'] if resident_res.data else ''
        if mobile:
            send_otp(mobile, code)
            supabase.table('otp_log').insert({
                'visitor_id': visitor_id,
                'mobile': mobile,
                'otp_code': code,
                'status': 'pending'
            }).execute()

        return jsonify({'success': True, 'id': visitor_id, 'otp': code})
    except Exception as e:
        logger.error(f'Error creating visitor request: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/visitor/verify-otp', methods=['POST'])
def api_visitor_verify_otp():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    visitor_id = body.get('visitor_id')
    mobile = body.get('mobile', '').strip()
    code = body.get('otp', '').strip()
    if not visitor_id or not mobile or not code:
        return jsonify({'error': 'visitor_id, mobile, and otp are required'}), 400
    try:
        # Find the latest pending OTP log
        res = supabase.table('otp_log').select('*').eq('visitor_id', visitor_id).eq('mobile', mobile).order('created_at', desc=True).limit(1).execute()
        if not res.data:
            return jsonify({'error': 'OTP request not found'}), 404
        log = res.data[0]
        if log['status'] != 'pending':
            return jsonify({'error': 'OTP already used or expired'}), 400
        if log['otp_code'] != code:
            return jsonify({'error': 'Invalid OTP'}), 400

        now = now_ist().isoformat()
        supabase.table('otp_log').update({'status': 'verified', 'verified_at': now}).eq('id', log['id']).execute()
        supabase.table('visitor_requests').update({
            'status': 'approved',
            'otp_verified_at': now
        }).eq('id', visitor_id).execute()

        return jsonify({'success': True, 'status': 'approved'})
    except Exception as e:
        logger.error(f'Error verifying OTP: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/visitor/requests')
@visitor_login_required
def api_visitor_requests():
    if not supabase:
        return jsonify([])
    try:
        resident = session.get('visitor_user')
        security = session.get('security_user')
        user = session.get('user')

        query = supabase.table('visitor_requests').select('*, residents(name, mobile, block, floor, flat), security_users(name)')
        if resident:
            query = query.eq('resident_id', resident['id'])
        # Admin/manager/supervisor from main app can see all
        res = query.order('created_at', desc=True).execute()

        rows = []
        for r in res.data or []:
            resident_data = r.get('residents') or {}
            security_data = r.get('security_users') or {}
            rows.append({
                'id': r['id'],
                'resident_id': r['resident_id'],
                'resident_name': resident_data.get('name'),
                'resident_mobile': resident_data.get('mobile'),
                'block': resident_data.get('block'),
                'floor': resident_data.get('floor'),
                'flat': resident_data.get('flat'),
                'security_name': security_data.get('name'),
                'visitor_name': r['visitor_name'],
                'visitor_mobile': r.get('visitor_mobile'),
                'purpose': r.get('purpose'),
                'visitor_count': r.get('visitor_count', 1),
                'vehicle_number': r.get('vehicle_number'),
                'id_proof_type': r.get('id_proof_type'),
                'remarks': r.get('remarks'),
                'status': r.get('status'),
                'entry_time': r.get('entry_time'),
                'exit_time': r.get('exit_time'),
                'created_at': r.get('created_at')
            })
        return jsonify(rows)
    except Exception as e:
        logger.error(f'Error fetching visitor requests: {e}')
        return jsonify([])


@app.route('/api/visitor/request/<req_id>', methods=['PATCH'])
@visitor_login_required
def api_visitor_request_patch(req_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    allowed = {}
    if 'status' in body and body['status'] in ('waiting','approved','rejected','inside','completed'):
        allowed['status'] = body['status']
        if body['status'] == 'inside':
            allowed['entry_time'] = now_ist().isoformat()
        if body['status'] == 'completed':
            allowed['exit_time'] = now_ist().isoformat()
    if not allowed:
        return jsonify({'error': 'Nothing to update'}), 400
    try:
        supabase.table('visitor_requests').update(allowed).eq('id', req_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error updating visitor request: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/visitor/dashboard-stats')
@visitor_login_required
def api_visitor_dashboard_stats():
    if not supabase:
        return jsonify({})
    try:
        today = now_ist().date().isoformat()
        base = supabase.table('visitor_requests').select('*')
        res = base.execute()
        rows = res.data or []
        today_rows = [r for r in rows if (r.get('created_at') or '').startswith(today)]
        # Overstay alert: visitors inside longer than 4 hours
        overstay_threshold = now_ist() - timedelta(hours=4)
        overstaying = []
        for r in rows:
            if r.get('status') == 'inside' and r.get('entry_time'):
                try:
                    entry = datetime.fromisoformat(r['entry_time'].replace('Z', '+00:00'))
                    if entry.tzinfo is None:
                        entry = entry.replace(tzinfo=IST)
                    if entry < overstay_threshold:
                        overstaying.append({
                            'id': r['id'],
                            'visitor_name': r.get('visitor_name'),
                            'entry_time': r.get('entry_time'),
                            'duration_hours': round((now_ist() - entry).total_seconds() / 3600, 1)
                        })
                except Exception:
                    pass
        result = {
            'total_today': len(today_rows),
            'pending': len([r for r in rows if r.get('status') == 'waiting']),
            'approved': len([r for r in rows if r.get('status') == 'approved']),
            'rejected': len([r for r in rows if r.get('status') == 'rejected']),
            'inside': len([r for r in rows if r.get('status') == 'inside']),
            'completed': len([r for r in rows if r.get('status') == 'completed']),
            'overstaying': overstaying
        }
        return jsonify(result)
    except Exception as e:
        logger.error(f'Error visitor dashboard stats: {e}')
        return jsonify({})


# ============================================================
# Main App Login & Me
# ============================================================

@app.route('/api/me')
def me():
    user = session.get('user')
    if isinstance(user, str):
        # Legacy session from before RBAC
        return jsonify({'user': user, 'role': 'admin', 'org_id': None})
    if isinstance(user, dict):
        return jsonify({'user': user.get('email'), 'role': user.get('role', 'supervisor'), 'org_id': user.get('org_id')})
    return jsonify({'user': None, 'role': None, 'org_id': None})


@app.route('/api/debug/ventures')
@requires_role('admin')
def api_debug_ventures():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        res = supabase.table('ventures').select('id, name, org_id').execute()
        return jsonify({
            'your_org_id': session['user'].get('org_id'),
            'ventures': res.data or []
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/login')
def login_page():
    if 'user' in session or 'security_user' in session or 'visitor_user' in session:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/visitor-portal')
def visitor_portal_page():
    if not session.get('security_user') and not session.get('visitor_user') and not session.get('user'):
        return redirect(url_for('login_page'))
    return render_template('visitor_portal.html')


@app.route('/api/health')
def api_health():
    """Public health check: reports Supabase connection and seeded user counts."""
    result = {'supabase_connected': bool(supabase)}
    if supabase:
        try:
            users = supabase.table('users').select('id', count='exact').execute()
            security = supabase.table('security_users').select('id', count='exact').execute()
            result['users_count'] = users.count if hasattr(users, 'count') else len(users.data or [])
            result['security_users_count'] = security.count if hasattr(security, 'count') else len(security.data or [])
        except Exception as e:
            result['error'] = str(e)
    return jsonify(result)


# ========================
# Cell Data API
# ========================

@app.route('/api/cells')
@requires_role('supervisor', 'manager', 'admin')
def api_cells():
    if not supabase:
        fallback = load_json_fallback('cells.json')
        return jsonify(fallback or {})
    try:
        query = supabase.table('cell_data').select('*')
        # Optional filter params for lazy-loading a specific venture/block/floor slice
        venture_id = request.args.get('venture_id')
        block = request.args.get('block')
        floor = request.args.get('floor')
        if venture_id:
            query = query.filter('data->>venture_id', 'eq', venture_id)
        if block:
            query = query.filter('data->>block', 'eq', block)
        if floor:
            query = query.filter('data->>floor', 'eq', str(floor))
        # Paginate to overcome Supabase's default 1000-row limit
        all_rows = []
        page_size = 1000
        offset = 0
        while True:
            res = query.range(offset, offset + page_size - 1).execute()
            all_rows.extend(res.data)
            if len(res.data) < page_size:
                break
            offset += page_size
        # Build response dict — migration 001 added UNIQUE constraint on id,
        # so no duplicates. Skip Python-side sort for performance.
        data = {}
        for row in all_rows:
            merged = {**(row.get('data') or {})}
            merged['id'] = row['id']
            data[row['id']] = merged
        return jsonify(data)
    except Exception as e:
        logger.error(f'Error fetching cells: {e}')
        fallback = load_json_fallback('cells.json')
        return jsonify(fallback or {})


@app.route('/api/cell/<cell_id>')
@requires_role('supervisor', 'manager', 'admin')
def api_cell(cell_id):
    if not supabase:
        fallback = load_json_fallback('cells.json') or {}
        cell = fallback.get(cell_id, {})
        return jsonify(cell if cell else {})
    try:
        res = supabase.table('cell_data').select('*').eq('id', cell_id).execute()
        if res.data:
            # Defensive: take most recently updated if duplicates exist
            row = max(res.data, key=lambda r: (r.get('data') or {}).get('updated_at', ''))
            merged = {**(row.get('data') or {})}
            merged['id'] = row['id']
            return jsonify(merged)
        return jsonify({})
    except Exception as e:
        logger.error(f'Error fetching cell {cell_id}: {e}')
        fallback = load_json_fallback('cells.json') or {}
        cell = fallback.get(cell_id, {})
        return jsonify(cell if cell else {})


@app.route('/api/cell/<cell_id>', methods=['POST'])
@requires_role_or_override('supervisor')
def api_cell_post(cell_id):
    body = request.get_json() or {}
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    color = body.get('color')
    if color is not None and color not in ('red', 'yellow', 'blue', 'green', ''):
        return jsonify({'error': f'Invalid color value: {color}'}), 400
    venture_id = body.get('venture_id')
    if venture_id and session['user'].get('role') not in ('admin', 'manager'):
        allowed = _allowed_ventures(session['user'])
        if venture_id not in allowed:
            return jsonify({'error': 'Forbidden'}), 403
    body = compress_images_in_data(body)
    try:
        prev_res = supabase.table('cell_data').select('data').eq('id', cell_id).execute()
        prev_color = None
        if prev_res.data:
            prev_data = prev_res.data[0].get('data') or {}
            prev_color = prev_data.get('color')
        supabase.table('cell_data').upsert({
            'id': cell_id,
            'data': body
        }, on_conflict='id').execute()
        return jsonify({'success': True, 'previous_color': prev_color})
    except Exception as e:
        logger.error(f'Error saving cell {cell_id}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/cells/batch', methods=['POST'])
@requires_role_or_override('supervisor')
def api_cells_batch():
    body = request.get_json() or {}
    cells = body.get('cells', [])
    if not cells:
        return jsonify({'success': True})
    for c in cells:
        d = c.get('data') or {}
        color = d.get('color')
        if color is not None and color not in ('red', 'yellow', 'blue', 'green', ''):
            return jsonify({'error': f'Invalid color value: {color}'}), 400
    if not supabase:
        return jsonify({'success': True, 'count': len(cells), 'note': 'read-only local mode'})
    rows = [{'id': c['id'], 'data': compress_images_in_data(c.get('data', {}))} for c in cells]
    try:
        downgraded = []
        for c in cells:
            cid = c.get('id')
            new_color = (c.get('data') or {}).get('color')
            if new_color and new_color != 'green':
                try:
                    prev_res = supabase.table('cell_data').select('data').eq('id', cid).execute()
                    if prev_res.data:
                        prev_color = (prev_res.data[0].get('data') or {}).get('color')
                        if prev_color == 'green':
                            downgraded.append(cid)
                except Exception:
                    pass
        supabase.table('cell_data').upsert(rows, on_conflict='id').execute()
        return jsonify({'success': True, 'count': len(rows), 'downgraded': downgraded})
    except Exception as e:
        logger.error(f'Error in batch upsert: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Ventures API
# ========================

@app.route('/api/ventures')
@login_required
def api_ventures():
    if not supabase:
        fallback = load_json_fallback('ventures.json')
        return jsonify(fallback or [])
    try:
        user = session['user']
        org_id = user.get('org_id')
        # Fetch ventures in this org PLUS legacy ventures with NULL org_id
        # (created before org_id filtering was added).
        res = supabase.table('ventures').select('*').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
        all_ventures = res.data or []
        # Filter out the synthetic '__all__' venture (used only for attendance)
        all_ventures = [v for v in all_ventures if v.get('id') != '__all__']

        # Auto-fix: assign org_id to legacy ventures that have NULL org_id
        # (done in background to avoid blocking the read response)
        null_org_ventures = [v for v in all_ventures if not v.get('org_id')]
        if null_org_ventures:
            import threading
            def _fix_org_ids():
                for v in null_org_ventures:
                    try:
                        supabase.table('ventures').update({'org_id': org_id}).eq('id', v['id']).execute()
                    except Exception:
                        pass
            threading.Thread(target=_fix_org_ids, daemon=True).start()

        def _venture_data(row):
            d = row.get('data')
            if isinstance(d, str):
                import json as _json
                try: d = _json.loads(d)
                except Exception: d = None
            if not d and row.get('id'):
                d = {'id': row['id'], 'name': row.get('name', row['id'])}
            return d

        # Filter out WAREHOUSE — it's a pseudo-venture only for inventory, not shown in dashboard
        all_ventures = [v for v in all_ventures if v.get('id') != 'WAREHOUSE']

        if user.get('role') in ('admin', 'manager'):
            result = [_venture_data(row) for row in all_ventures if row.get('data') or row.get('id')]
            return jsonify(result)
        else:
            allowed = _allowed_ventures(user)
            return jsonify([_venture_data(row) for row in all_ventures if row.get('data') and (row['id'] in allowed or not row.get('org_id'))])
    except Exception as e:
        logger.error(f'Error fetching ventures: {e}')
        fallback = load_json_fallback('ventures.json')
        return jsonify(fallback or [])


@app.route('/api/ventures', methods=['POST'])
@requires_role('admin')
def api_ventures_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        body = request.get_json() or []
        if not body:
            return jsonify({'error': 'Refusing to replace ventures with an empty list'}), 400

        # Defence-in-depth: bulk save must not silently drop existing ventures.
        # Legitimate edits should use POST /api/venture/<id>; this endpoint is
        # reserved for first-run seeding and explicit full-restore operations.
        existing = supabase.table('ventures').select('id').execute()
        existing_ids = {row['id'] for row in existing.data}
        incoming_ids = {v.get('id') for v in body if v.get('id')}
        force = request.args.get('force', 'false').lower() == 'true'

        if existing_ids and not incoming_ids.issuperset(existing_ids) and not force:
            missing = sorted(existing_ids - incoming_ids)
            return jsonify({
                'error': 'Bulk save would drop existing ventures',
                'missing': missing,
                'hint': 'Use per-record POST /api/venture/<id> for edits, DELETE /api/venture/<id> for removal, or pass force=true for a full restore.'
            }), 409

        org_id = session['user'].get('org_id')
        for v in body:
            supabase.table('ventures').upsert({
                'id': v['id'],
                'name': v.get('name'),
                'org_id': org_id,
                'data': v
            }, on_conflict='id').execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error saving ventures: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/venture/<venture_id>', methods=['POST'])
@requires_role('manager', 'admin')
def api_venture_post(venture_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        v = request.get_json() or {}
        v['id'] = venture_id
        org_id = session['user'].get('org_id')
        name = v.get('name') or (v.get('data') or {}).get('name')
        supabase.table('ventures').upsert({
            'id': venture_id,
            'name': name,
            'org_id': org_id,
            'data': v
        }, on_conflict='id').execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error saving venture {venture_id}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/venture/<venture_id>', methods=['DELETE'])
@requires_role('manager', 'admin')
def api_venture_delete(venture_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('ventures').delete().eq('id', venture_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting venture {venture_id}: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/ventures/apply-settings', methods=['POST'])
@requires_role('supervisor', 'manager', 'admin')
def api_ventures_apply_settings():
    """Apply configuration changes to a single venture or all ventures.

    Body: {
        scope: 'selected' | 'all',
        venture_id: str (required when scope='selected'),
        settings: {
            flat_view_items: [...],
            super_structure_items: [...],
            work_categories: {...},
            blocks: [...]
        }
    }
    Only the fields present in `settings` are overwritten; other venture data is preserved.
    """
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    data = request.get_json() or {}
    scope = data.get('scope', 'selected')
    settings = data.get('settings', {})

    if not settings:
        return jsonify({'error': 'No settings provided'}), 400

    # scope='all' is a destructive, company-wide action — admin only
    if scope == 'all' and session['user'].get('role') != 'admin':
        return jsonify({'error': 'Only admins can apply settings to all ventures'}), 403

    # Valid setting keys that can be applied
    valid_keys = {'flat_view_items', 'super_structure_items', 'work_categories', 'blocks'}
    apply_keys = set(settings.keys()) & valid_keys
    if not apply_keys:
        return jsonify({'error': 'No valid setting keys provided'}), 400

    user_email = session['user'].get('email', 'unknown')
    org_id = session['user'].get('org_id')

    try:
        if scope == 'all':
            res = supabase.table('ventures').select('*').eq('org_id', org_id).execute()
            if not res.data:
                return jsonify({'error': 'No ventures found'}), 404
            updated = 0
            for row in res.data:
                vdata = row.get('data') or {}
                if isinstance(vdata, str):
                    import json as _json
                    try:
                        vdata = _json.loads(vdata)
                    except Exception:
                        vdata = {}
                old_data = {k: vdata.get(k) for k in apply_keys}
                for key in apply_keys:
                    vdata[key] = settings[key]
                supabase.table('ventures').update({'data': vdata}).eq('id', row['id']).execute()
                # Audit log: preserve the previous config for recovery
                try:
                    supabase.table('audit_log').insert({
                        'org_id': org_id,
                        'user_email': user_email,
                        'action': 'apply_settings_all',
                        'target_id': row['id'],
                        'old_data': old_data,
                        'new_data': {k: settings[k] for k in apply_keys},
                    }).execute()
                except Exception as audit_err:
                    logger.warning(f'Audit log insert failed (non-fatal): {audit_err}')
                updated += 1
            return jsonify({'success': True, 'updated': updated})
        else:
            venture_id = data.get('venture_id', '').strip()
            if not venture_id:
                return jsonify({'error': 'venture_id is required for selected scope'}), 400
            res = supabase.table('ventures').select('*').eq('id', venture_id).execute()
            if not res.data:
                return jsonify({'error': 'Venture not found'}), 404
            row = res.data[0]
            vdata = row.get('data') or {}
            if isinstance(vdata, str):
                import json as _json
                try:
                    vdata = _json.loads(vdata)
                except Exception:
                    vdata = {}
            old_data = {k: vdata.get(k) for k in apply_keys}
            for key in apply_keys:
                vdata[key] = settings[key]
            supabase.table('ventures').update({'data': vdata}).eq('id', venture_id).execute()
            try:
                supabase.table('audit_log').insert({
                    'org_id': org_id,
                    'user_email': user_email,
                    'action': 'apply_settings_single',
                    'target_id': venture_id,
                    'old_data': old_data,
                    'new_data': {k: settings[k] for k in apply_keys},
                }).execute()
            except Exception as audit_err:
                logger.warning(f'Audit log insert failed (non-fatal): {audit_err}')
            return jsonify({'success': True, 'updated': 1})
    except Exception as e:
        logger.error(f'Error applying settings: {e}')
        return jsonify({'error': str(e)}), 500


# ============================================================
# Phase 2: Dual-write helpers (best-effort writes to v2 tables)
# Source of truth remains the original JSONB tables in this phase.
# ============================================================
_DEFAULT_ORG_ID = '11111111-1111-1111-1111-111111111111'

def _dual_write_vendor_v2(vendor):
    """Best-effort upsert of a vendor JSONB row into vendors_v2."""
    try:
        bank_details = {}
        for k in ('accountHolder', 'accountNo', 'bankName', 'ifsc'):
            if vendor.get(k):
                bank_details[k] = vendor.get(k)
        row = {
            'org_id': _DEFAULT_ORG_ID,
            'legacy_id': vendor.get('id') or None,
            'venture_id': vendor.get('ventureId') or vendor.get('venture_id'),
            'name': vendor.get('name', ''),
            'gstin': vendor.get('gstin') or None,
            'contact_phone': vendor.get('phone') or vendor.get('contact') or None,
            'contact_email': vendor.get('email') or None,
            'bank_details': bank_details if bank_details else None,
            'remarks': vendor.get('notes') or None,
            'status': 'active',
            'updated_at': 'now()',
        }
        existing = supabase.table('vendors_v2').select('id').eq('legacy_id', vendor.get('id') or '').execute()
        matches = existing.data or []
        if not matches and vendor.get('name'):
            existing = supabase.table('vendors_v2').select('id').eq('org_id', _DEFAULT_ORG_ID).ilike('name', vendor.get('name', '')).execute()
            matches = existing.data or []
        if matches:
            row_id = matches[0]['id']
            supabase.table('vendors_v2').update(row).eq('id', row_id).execute()
        else:
            import uuid as _uuid
            row_id = str(_uuid.uuid4())
            row['id'] = row_id
            supabase.table('vendors_v2').insert(row).execute()
    except Exception as e:
        logger.error(f'Dual-write vendor_v2 failed for vendor id={vendor.get("id")}: {e}')

def _dual_write_po_v2(po):
    """Best-effort upsert of a purchase order JSONB row into purchase_orders_v2."""
    try:
        total = po.get('billAmount') or po.get('quotedAmount') or 0
        row = {
            'org_id': _DEFAULT_ORG_ID,
            'legacy_id': po.get('id') or None,
            'venture_id': po.get('ventureId') or po.get('venture_id') or None,
            'po_number': po.get('poNumber') or None,
            'status': po.get('status') or 'draft',
            'total_amount': float(total) if total else None,
            'received_status': 'not_received',
            'created_by': po.get('createdBy') or None,
            'updated_at': 'now()',
        }
        existing = supabase.table('purchase_orders_v2').select('id').eq('legacy_id', po.get('id') or '').execute()
        matches = existing.data or []
        if not matches and po.get('poNumber'):
            existing = supabase.table('purchase_orders_v2').select('id').eq('org_id', _DEFAULT_ORG_ID).eq('po_number', po.get('poNumber') or '').execute()
            matches = existing.data or []
        if matches:
            row_id = matches[0]['id']
            supabase.table('purchase_orders_v2').update(row).eq('id', row_id).execute()
        else:
            import uuid as _uuid
            row_id = str(_uuid.uuid4())
            row['id'] = row_id
            supabase.table('purchase_orders_v2').insert(row).execute()
    except Exception as e:
        logger.error(f'Dual-write po_v2 failed for PO id={po.get("id")}: {e}')

def _dual_write_invoice_v2(inv):
    """Best-effort upsert of an invoice JSONB row into invoices_v2."""
    try:
        row = {
            'org_id': _DEFAULT_ORG_ID,
            'legacy_id': inv.get('id') or None,
            'venture_id': inv.get('ventureId') or inv.get('venture_id') or None,
            'invoice_number': inv.get('id') or None,
            'total_amount': float(inv.get('amount') or 0) or None,
            'paid_amount': 0,
            'status': inv.get('paymentMode') and 'paid' or 'pending',
            'due_date': inv.get('purchaseDate') or None,
        }
        existing = supabase.table('invoices_v2').select('id').eq('legacy_id', inv.get('id') or '').execute()
        matches = existing.data or []
        if not matches and inv.get('id'):
            existing = supabase.table('invoices_v2').select('id').eq('org_id', _DEFAULT_ORG_ID).eq('invoice_number', inv.get('id') or '').execute()
            matches = existing.data or []
        if matches:
            row_id = matches[0]['id']
            supabase.table('invoices_v2').update(row).eq('id', row_id).execute()
        else:
            import uuid as _uuid
            row_id = str(_uuid.uuid4())
            row['id'] = row_id
            supabase.table('invoices_v2').insert(row).execute()
    except Exception as e:
        logger.error(f'Dual-write invoice_v2 failed for invoice id={inv.get("id")}: {e}')


# ========================
# Invoices API
# ========================

@app.route('/api/invoices')
@requires_role('manager', 'admin')
def api_invoices():
    if not supabase:
        fallback = load_json_fallback('invoices.json')
        return jsonify(fallback or [])
    try:
        venture_id = request.args.get('venture_id')
        query = supabase.table('invoices').select('*')
        if venture_id:
            query = query.filter('data->>ventureId', 'eq', venture_id)
        res = query.execute()
        return jsonify([row['data'] for row in res.data])
    except Exception as e:
        logger.error(f'Error fetching invoices: {e}')
        fallback = load_json_fallback('invoices.json')
        return jsonify(fallback or [])


@app.route('/api/invoice', methods=['POST'])
@requires_role('manager', 'admin')
def api_invoice_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        inv = request.get_json() or {}
        supabase.table('invoices').upsert({
            'id': inv['id'],
            'data': inv
        }, on_conflict='id').execute()
        _dual_write_invoice_v2(inv)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error saving invoice: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/invoice/<inv_id>', methods=['DELETE'])
@requires_role('manager', 'admin')
def api_invoice_delete(inv_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('invoices').delete().eq('id', inv_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting invoice {inv_id}: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Purchase Orders API
# ========================

@app.route('/api/pos')
@requires_role('manager', 'admin')
def api_pos():
    if not supabase:
        fallback = load_json_fallback('pos.json')
        return jsonify(fallback or [])
    try:
        venture_id = request.args.get('venture_id')
        query = supabase.table('purchase_orders').select('*')
        if venture_id:
            query = query.filter('data->>ventureId', 'eq', venture_id)
        res = query.execute()
        return jsonify([row['data'] for row in res.data])
    except Exception as e:
        logger.error(f'Error fetching POs: {e}')
        fallback = load_json_fallback('pos.json')
        return jsonify(fallback or [])


@app.route('/api/po', methods=['POST'])
@requires_role('manager', 'admin')
def api_po_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        po = request.get_json() or {}
        po_number = (po.get('poNumber') or '').strip()
        if po_number:
            po_lower = po_number.lower()
            existing = supabase.table('purchase_orders').select('id,data').execute()
            for row in (existing.data or []):
                row_num = ((row.get('data') or {}).get('poNumber') or '').strip().lower()
                if row_num == po_lower and row['id'] != po.get('id'):
                    return jsonify({'error': f'PO "{po_number}" already exists'}), 409
        supabase.table('purchase_orders').upsert({
            'id': po['id'],
            'data': po
        }, on_conflict='id').execute()
        _dual_write_po_v2(po)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error saving PO: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/po/<po_id>', methods=['DELETE'])
@requires_role('manager', 'admin')
def api_po_delete(po_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('purchase_orders').delete().eq('id', po_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting PO {po_id}: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Vendors API
# ========================

@app.route('/api/vendors')
@requires_role_or_override('supervisor')
def api_vendors():
    if not supabase:
        fallback = load_json_fallback('vendors.json')
        return jsonify(fallback or [])
    try:
        org_id = session['user'].get('org_id')
        res = supabase.table('vendors').select('*').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
        return jsonify([row['data'] for row in (res.data or [])])
    except Exception as e:
        logger.error(f'Error fetching vendors: {e}')
        fallback = load_json_fallback('vendors.json')
        return jsonify(fallback or [])


@app.route('/api/vendor', methods=['POST'])
@requires_role('manager', 'admin')
def api_vendor_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        vendor = request.get_json() or {}
        name = (vendor.get('name') or '').strip()
        org_id = session['user'].get('org_id')
        if name:
            name_lower = name.lower()
            existing = supabase.table('vendors').select('id,data').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
            for row in (existing.data or []):
                row_name = ((row.get('data') or {}).get('name') or '').strip().lower()
                if row_name == name_lower and row['id'] != vendor.get('id'):
                    return jsonify({'error': f'Vendor "{name}" already exists'}), 409
        supabase.table('vendors').upsert({
            'id': vendor['id'],
            'org_id': org_id,
            'name': name,
            'contact_phone': vendor.get('phone') or None,
            'data': vendor
        }, on_conflict='id').execute()
        logger.info(f'[vendor-post] Saved vendor id={vendor["id"]}, name={name}, org_id={org_id}')
        _dual_write_vendor_v2(vendor)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error saving vendor: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/vendor/<vendor_id>', methods=['DELETE'])
@requires_role('manager', 'admin')
def api_vendor_delete(vendor_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('vendors').delete().eq('id', vendor_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting vendor {vendor_id}: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Settings API
# ========================

@app.route('/api/settings/<key>')
@requires_role('manager', 'admin')
def api_settings_get(key):
    if not supabase:
        return jsonify(None)
    try:
        res = supabase.table('settings').select('*').eq('key', key).execute()
        if res.data:
            return jsonify(res.data[0]['value'])
        return jsonify(None)
    except Exception as e:
        logger.error(f'Error fetching setting {key}: {e}')
        return jsonify(None)


@app.route('/api/settings/<key>', methods=['POST'])
@requires_role('manager', 'admin')
def api_settings_post(key):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        value = request.get_json()
        supabase.table('settings').upsert({
            'key': key,
            'value': value
        }, on_conflict='key').execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error saving setting {key}: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# User Preferences API (per-user work view layout)
# ========================

@app.route('/api/user-prefs')
@requires_role('supervisor', 'manager', 'admin')
def api_user_prefs_get():
    if not supabase:
        return jsonify({})
    user = session.get('user')
    # Legacy string-session admin has no 'id' — return empty prefs
    if isinstance(user, str) or not isinstance(user, dict) or not user.get('id'):
        return jsonify({})
    try:
        res = supabase.table('user_prefs').select('pref_value').eq('user_id', user['id']).eq('pref_key', 'work_view_layout').execute()
        if res.data:
            return jsonify(res.data[0]['pref_value'])
        return jsonify({})
    except Exception as e:
        logger.error(f'Error fetching user prefs: {e}')
        return jsonify({})


@app.route('/api/user-prefs', methods=['POST'])
@requires_role('supervisor', 'manager', 'admin')
def api_user_prefs_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    user = session.get('user')
    # Legacy string-session admin has no 'id' — no-op
    if isinstance(user, str) or not isinstance(user, dict) or not user.get('id'):
        return jsonify({'success': True, 'note': 'legacy session — prefs not saved'})
    try:
        value = request.get_json() or {}
        supabase.table('user_prefs').upsert({
            'user_id': user['id'],
            'pref_key': 'work_view_layout',
            'pref_value': value
        }, on_conflict='user_id,pref_key').execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error saving user prefs: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Attendance API (replaces Payroll)
# Supports "All Ventures" mode (venture_id='__all__') for GET requests.
# POST/DELETE always require a specific venture_id for ownership validation.
# ========================

def _attendance_table_error(e):
    """Return a user-friendly error if the attendance table doesn't exist."""
    err_str = str(e)
    if 'PGRST205' in err_str or 'Could not find the table' in err_str or 'schema cache' in err_str:
        return jsonify({
            'error': 'The attendance table does not exist in the database. '
                     'Please run migration 019_attendance.sql in the Supabase SQL Editor, '
                     'or set DATABASE_URL in .env for auto-migration.'
        }), 500
    return jsonify({'error': err_str}), 500


@app.route('/api/attendance')
@requires_role('supervisor', 'manager', 'admin')
def api_attendance_get():
    if not supabase:
        return jsonify([])
    user = session.get('user')
    # Legacy string-session admin - treat as full access
    if isinstance(user, str):
        user = {'role': 'admin'}
    if not isinstance(user, dict):
        return jsonify({'error': 'Invalid session'}), 403

    venture_id = request.args.get('venture_id', '')
    month = request.args.get('month', '')
    if not month:
        return jsonify({'error': 'month is required'}), 400

    allowed_ventures = _allowed_ventures(user)

    try:
        query = supabase.table('attendance').select('*').eq('month', month)
        if venture_id and venture_id != '__all__':
            # Specific venture - validate ownership, return only this venture's rows
            if venture_id not in allowed_ventures:
                return jsonify({'error': 'Forbidden'}), 403
            query = query.eq('venture_id', venture_id)
        else:
            # "All Ventures" mode - filter to only allowed ventures plus __all__ rows
            if not allowed_ventures:
                return jsonify([])
            venture_list = list(allowed_ventures)
            venture_list.append('__all__')
            query = query.in_('venture_id', venture_list)
        res = query.execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching attendance: {e}')
        return _attendance_table_error(e)


@app.route('/api/attendance/history')
@requires_role('supervisor', 'manager', 'admin')
def api_attendance_history():
    """Fetch all attendance records for a specific employee across all months."""
    if not supabase:
        return jsonify([])
    user = session.get('user')
    if isinstance(user, str):
        user = {'role': 'admin'}
    if not isinstance(user, dict):
        return jsonify({'error': 'Invalid session'}), 403

    employee_name = request.args.get('employee_name', '').strip()
    venture_id = request.args.get('venture_id', '').strip()
    if not employee_name:
        return jsonify({'error': 'employee_name is required'}), 400

    allowed_ventures = _allowed_ventures(user)
    try:
        query = supabase.table('attendance').select('*').ilike('employee_name', employee_name)
        if venture_id and venture_id != '__all__':
            if venture_id not in allowed_ventures:
                return jsonify({'error': 'Forbidden'}), 403
            query = query.eq('venture_id', venture_id)
        else:
            if not allowed_ventures:
                return jsonify([])
            venture_list = list(allowed_ventures)
            venture_list.append('__all__')
            query = query.in_('venture_id', venture_list)
        res = query.order('month', desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching attendance history: {e}')
        return _attendance_table_error(e)


@app.route('/api/attendance', methods=['POST'])
@requires_role('supervisor', 'manager', 'admin')
def api_attendance_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    user = session.get('user')
    if isinstance(user, str):
        user = {'role': 'admin', 'email': user}
    if not isinstance(user, dict):
        return jsonify({'error': 'Invalid session'}), 403

    body = request.get_json() or {}
    venture_id = body.get('venture_id', '')
    employee_name = body.get('employee_name', '').strip()
    month = body.get('month', '')
    if not venture_id or not employee_name or not month:
        return jsonify({'error': 'venture_id, employee_name, and month are required'}), 400

    # Security: validate venture ownership (__all__ is always allowed for authenticated users)
    if venture_id != '__all__' and venture_id not in _allowed_ventures(user):
        return jsonify({'error': 'Forbidden'}), 403

    try:
        row_data = {
            'venture_id': venture_id,
            'employee_name': employee_name,
            'month': month,
            'role': body.get('role', ''),
            'base_salary': body.get('base_salary', 0),
            'present_days': body.get('present_days', 0),
            'absent_days': body.get('absent_days', 0),
            'daily_marking': body.get('daily_marking', {}),
            'created_by': user.get('email', ''),
        }
        res = supabase.table('attendance').upsert(row_data, on_conflict='venture_id,employee_name,month').execute()
        return jsonify({'success': True, 'data': res.data[0] if res.data else None})
    except Exception as e:
        logger.error(f'Error saving attendance: {e}')
        return _attendance_table_error(e)


@app.route('/api/attendance/<row_id>', methods=['DELETE'])
@requires_role('supervisor', 'manager', 'admin')
def api_attendance_delete(row_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    user = session.get('user')
    if isinstance(user, str):
        user = {'role': 'admin'}
    if not isinstance(user, dict):
        return jsonify({'error': 'Invalid session'}), 403

    try:
        supabase.table('attendance').delete().eq('id', row_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting attendance: {e}')
        return _attendance_table_error(e)


# ========================
# Inventory API
# ========================

@app.route('/api/materials')
@requires_role_or_override('supervisor')
def api_materials():
    if not supabase:
        return jsonify([]), 500
    venture_id = request.args.get('venture_id')
    is_global = request.args.get('global', 'false').lower() == 'true'
    q = supabase.table('materials').select('*')
    if is_global:
        q = q.is_('venture_id', 'null')
    elif venture_id:
        allowed = _allowed_ventures(session['user'])
        allowed_with_wh = allowed | {'WAREHOUSE'}
        if venture_id not in allowed_with_wh:
            return jsonify({'error': 'Forbidden'}), 403
        if venture_id == 'WAREHOUSE':
            q = q.or_(f'venture_id.eq.WAREHOUSE,venture_id.is.null')
        else:
            q = q.or_(f'venture_id.eq.{venture_id},venture_id.is.null,venture_id.eq.WAREHOUSE')
    res = q.execute()
    return jsonify(res.data or [])


@app.route('/api/materials/categories')
@requires_role_or_override('supervisor')
def api_material_categories():
    """Return distinct categories from materials table, scoped to venture + global."""
    if not supabase:
        return jsonify([]), 500
    venture_id = request.args.get('venture_id')
    allowed = _allowed_ventures(session['user'])
    allowed_with_wh = allowed | {'WAREHOUSE'}
    q = supabase.table('materials').select('category')
    if venture_id:
        if venture_id not in allowed_with_wh:
            return jsonify({'error': 'Forbidden'}), 403
        if venture_id == 'WAREHOUSE':
            q = q.or_(f'venture_id.eq.WAREHOUSE,venture_id.is.null')
        else:
            q = q.or_(f'venture_id.eq.{venture_id},venture_id.is.null,venture_id.eq.WAREHOUSE')
    else:
        q = q.in_('venture_id', list(allowed_with_wh))
    res = q.execute()
    cats = sorted(set(r['category'] for r in (res.data or []) if r.get('category')))
    return jsonify(cats)


@app.route('/api/material', methods=['POST'])
@requires_role_or_override('supervisor')
def api_material_post():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    # Only admin may create/edit materials
    if session['user'].get('role') != 'admin':
        return jsonify({'error': 'Only admins can create or modify materials'}), 403
    m = request.get_json() or {}
    if not m.get('id'):
        m['id'] = str(__import__('uuid').uuid4())
    # Global materials (venture_id null) restricted to admin/manager
    if m.get('venture_id') is None:
        if session['user'].get('role') not in ('admin', 'manager'):
            return jsonify({'error': 'Only admins and managers can create global materials'}), 403
        # Enforce (name, unit) uniqueness for global materials
        name = (m.get('name') or '').strip().lower()
        unit = (m.get('unit') or '').strip().lower()
        if name and unit:
            existing = supabase.table('materials').select('id').is_('venture_id', 'null').ilike('name', name).ilike('unit', unit).execute()
            if existing.data and not any(r['id'] == m['id'] for r in existing.data):
                return jsonify({'error': f'A global material named "{m.get("name")}" with unit "{m.get("unit")}" already exists'}), 409
    supabase.table('materials').upsert(m, on_conflict='id').execute()
    return jsonify({'success': True})


@app.route('/api/material/<material_id>', methods=['DELETE'])
@requires_role_or_override('supervisor')
def api_material_delete(material_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    # Check if material is global (venture_id null)
    check = supabase.table('materials').select('venture_id').eq('id', material_id).execute()
    if check.data:
        mat = check.data[0]
        if mat.get('venture_id') is None:
            if session['user'].get('role') not in ('admin', 'manager'):
                return jsonify({'error': 'Only admins and managers can delete global materials'}), 403
        else:
            allowed = _allowed_ventures(session['user'])
            if mat['venture_id'] not in allowed:
                return jsonify({'error': 'Forbidden'}), 403
    supabase.table('materials').delete().eq('id', material_id).execute()
    return jsonify({'success': True})


@app.route('/api/stock')
@requires_role_or_override('supervisor')
def api_stock():
    if not supabase:
        return jsonify([]), 500
    allowed = _allowed_ventures(session['user'])
    allowed_with_wh = allowed | {'WAREHOUSE'}
    venture_id = request.args.get('venture_id')
    if venture_id:
        if venture_id not in allowed_with_wh:
            return jsonify({'error': 'Forbidden'}), 403
    q = supabase.table('stock_ledger').select('*')
    if venture_id:
        q = q.eq('venture_id', venture_id)
    else:
        q = q.in_('venture_id', list(allowed_with_wh))
    for f in ['material_id', 'entry_type', 'block', 'floor', 'vendor_id']:
        v = request.args.get(f)
        if v:
            q = q.eq(f, v)
    from_date = request.args.get('from')
    to_date = request.args.get('to')
    if from_date:
        q = q.gte('entry_date', from_date)
    if to_date:
        q = q.lte('entry_date', to_date)

    # Supabase caps single responses at 1000 rows; paginate to fetch all.
    all_data = []
    chunk_size = 1000
    start = 0
    while True:
        chunk = q.range(start, start + chunk_size - 1).execute()
        rows = chunk.data or []
        if not rows:
            break
        all_data.extend(rows)
        if len(rows) < chunk_size:
            break
        start += chunk_size

    return jsonify(all_data)


@app.route('/api/stock', methods=['POST'])
@requires_role_or_override('supervisor')
def api_stock_post():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    entry = request.get_json() or {}
    entry_type = entry.get('entry_type', '')
    venture_id = entry.get('venture_id')
    if venture_id:
        allowed = _allowed_ventures(session['user'])
        if venture_id not in allowed and venture_id != 'WAREHOUSE':
            return jsonify({'error': 'Forbidden'}), 403
    if entry_type == 'IN':
        rate = float(entry.get('rate') or 0)
        if rate <= 0:
            return jsonify({'error': 'Rate is required for Stock In entries and must be > 0'}), 400
        entry['cost_per_unit'] = rate
    if not entry.get('id'):
        entry['id'] = str(__import__('uuid').uuid4())
    supabase.table('stock_ledger').upsert(entry, on_conflict='id').execute()
    return jsonify({'success': True})


@app.route('/api/stock/next-entry', methods=['POST'])
@requires_role_or_override('supervisor')
def api_stock_next_entry():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    org_id = session['user'].get('org_id')
    user_email = (session.get('user') or {}).get('email', '')
    venture_id = body.get('venture_id')
    material_id = body.get('material_id')
    material_name = (body.get('material_name') or '').strip()
    unit = (body.get('unit') or 'pcs').strip()
    entry_date = body.get('entry_date')
    purchase = float(body.get('purchase') or 0)
    usage = float(body.get('usage') or 0)
    rate = float(body.get('rate') or 0)
    vendor_id = body.get('vendor_id') or None
    invoice_no = (body.get('invoice_no') or '').strip() or None
    is_gst = bool(body.get('is_gst'))
    remarks = (body.get('remarks') or '').strip() or None

    if venture_id:
        allowed = _allowed_ventures(session['user'])
        if venture_id not in allowed and venture_id != 'WAREHOUSE':
            return jsonify({'error': 'Forbidden'}), 403
    if not material_id or not entry_date:
        return jsonify({'error': 'material_id and entry_date are required'}), 400
    if purchase == 0 and usage == 0:
        return jsonify({'error': 'purchase or usage must be > 0'}), 400

    # Ensure material exists in the target venture (or globally)
    mat = supabase.table('materials').select('id,name,category,unit').eq('id', material_id).limit(1).execute()
    if not mat.data:
        return jsonify({'error': 'Material not found'}), 404
    mat = mat.data[0]

    entries = body.get('entries') or []
    saved_stock_ids = []
    for e in entries:
        e['id'] = e.get('id') or str(__import__('uuid').uuid4())
        e['created_by'] = user_email
        if e.get('entry_type') == 'IN' and float(e.get('rate') or 0) > 0:
            e['cost_per_unit'] = float(e['rate'])
        supabase.table('stock_ledger').upsert(e, on_conflict='id').execute()
        saved_stock_ids.append(e['id'])

    # Auto-create Day Book purchase when a purchase quantity is present
    if purchase > 0 and rate > 0:
        vendor_name = ''
        if vendor_id:
            v = supabase.table('vendors').select('id,data').eq('id', vendor_id).maybe_single().execute()
            if v.data:
                vendor_name = (v.data.get('data') or {}).get('name') or v.data.get('name') or vendor_id
        if not vendor_id or not vendor_name:
            vendor_name = 'Inventory Entry'
        purchase_body = {
            'id': str(__import__('uuid').uuid4()),
            'org_id': org_id,
            'venture_id': venture_id,
            'invoice_date': entry_date,
            'invoice_no': invoice_no or 'AUTO-' + str(__import__('uuid').uuid4())[:8].upper(),
            'is_gst': is_gst,
            'received_date': entry_date,
            'vendor_id': vendor_id,
            'vendor_name': vendor_name,
            'material_name': material_name,
            'category': mat.get('category'),
            'qty': purchase,
            'unit': unit,
            'rate': rate,
            'amount': purchase * rate,
            'remarks': remarks or 'Auto-created from inventory next entry',
            'created_by': user_email
        }
        try:
            supabase.table('inventory_purchases').upsert(purchase_body, on_conflict='id').execute()
        except Exception as e:
            logger.warning(f'Auto-create day book purchase failed (non-fatal): {e}')

    return jsonify({'success': True, 'stock_ids': saved_stock_ids})


@app.route('/api/stock/summary')
@requires_role_or_override('supervisor')
def api_stock_summary():
    if not supabase:
        return jsonify([]), 500
    venture_id = request.args.get('venture_id')
    allowed = _allowed_ventures(session['user'])
    q = supabase.table('stock_balance').select('*')
    if venture_id:
        if venture_id not in allowed and venture_id != 'WAREHOUSE':
            return jsonify({'error': 'Forbidden'}), 403
        q = q.eq('venture_id', venture_id)
    else:
        allowed_with_wh = allowed | {'WAREHOUSE'}
        if not allowed_with_wh:
            return jsonify([])
        q = q.in_('venture_id', list(allowed_with_wh))
    res = q.execute()
    return jsonify(res.data or [])


@app.route('/api/stock/location-report')
@requires_role_or_override('supervisor')
def api_stock_location_report():
    if not supabase:
        return jsonify([]), 500
    allowed = _allowed_ventures(session['user'])
    venture_id = request.args.get('venture_id')
    if venture_id:
        if venture_id not in allowed and venture_id != 'WAREHOUSE':
            return jsonify({'error': 'Forbidden'}), 403
    material_id = request.args.get('material_id')
    q = supabase.table('stock_ledger').select('*').eq('entry_type', 'OUT')
    if venture_id:
        q = q.eq('venture_id', venture_id)
    else:
        q = q.in_('venture_id', list(allowed))
    if material_id:
        q = q.eq('material_id', material_id)
    res = q.execute()
    return jsonify(res.data or [])


@app.route('/api/stock/vendor-report')
@requires_role_or_override('supervisor')
def api_stock_vendor_report():
    if not supabase:
        return jsonify([]), 500
    allowed = _allowed_ventures(session['user'])
    allowed_with_wh = allowed | {'WAREHOUSE'}
    vendor_id = request.args.get('vendor_id')
    venture_id = request.args.get('venture_id')
    q = supabase.table('stock_ledger').select('*').eq('entry_type', 'IN')
    if venture_id:
        if venture_id not in allowed_with_wh:
            return jsonify({'error': 'Forbidden'}), 403
        q = q.eq('venture_id', venture_id)
    else:
        q = q.in_('venture_id', list(allowed_with_wh))
    if vendor_id:
        q = q.eq('vendor_id', vendor_id)
    res = q.execute()
    return jsonify(res.data or [])


# ============================================================
# Inventory Purchase + Daily Inventory Modules (migration 021)
# ============================================================

def _compute_daily_purchase(org_id, venture_id, material_name, category_type, entry_date):
    """SUM of inventory_purchases.qty matching received_date=entry_date + material + type.
    Single source of truth — the daily register's purchase column is derived, not manual."""
    if not supabase:
        return 0
    q = supabase.table('inventory_purchases').select('qty').eq('org_id', org_id)
    q = q.eq('material_name', material_name).eq('received_date', entry_date)
    if category_type:
        q = q.eq('category_type', category_type)
    else:
        q = q.is_('category_type', 'null')
    res = q.execute()
    return sum(float(r.get('qty') or 0) for r in (res.data or []))


def _ensure_material_master(org_id, name, unit=None):
    """Auto-create material master row if new (case-insensitive dedup). Returns None."""
    if not supabase or not name:
        return
    existing = supabase.table('inventory_materials').select('id,name').eq('org_id', org_id).execute()
    for r in (existing.data or []):
        if (r.get('name') or '').strip().lower() == name.strip().lower():
            return  # already exists
    try:
        supabase.table('inventory_materials').insert({
            'org_id': org_id, 'name': name.strip(), 'unit': unit
        }).execute()
    except Exception:
        # race condition — another request created it; ignore
        pass


def _get_or_create_material_for_stock(material_name, venture_id, category=None, unit=None):
    """Find or create a material in the main materials table for stock_ledger integration."""
    if not supabase or not material_name:
        return None
    existing = supabase.table('materials').select('id,name,category,unit').execute()
    for r in (existing.data or []):
        if (r.get('name') or '').strip().lower() == material_name.strip().lower():
            return r
    # create as venture-specific material so it shows in that venture's inventory
    new_id = str(__import__('uuid').uuid4())
    new_row = {
        'id': new_id,
        'name': material_name.strip(),
        'category': category or 'Uncategorized',
        'unit': unit or 'pcs',
        'min_threshold': 0,
        'venture_id': None if venture_id == 'WAREHOUSE' else venture_id
    }
    try:
        supabase.table('materials').insert(new_row).execute()
        return new_row
    except Exception as e:
        logger.warning(f'Create material for stock failed: {e}')
        return None


def _auto_create_daily_entry(org_id, material_name, category_type, entry_date, user_email=''):
    """Auto-create or update a daily_inventory row so purchases appear in the register.
    If a row already exists for this date+material+type, leave it (purchase is recomputed live).
    If not, create one with usage_qty=0 so the stock received shows up."""
    if not supabase or not material_name or not entry_date:
        return
    # Check if a daily entry already exists for this date + material + type
    existing = supabase.table('daily_inventory').select('id').eq('org_id', org_id)
    existing = existing.eq('material_name', material_name).eq('entry_date', entry_date)
    if category_type:
        existing = existing.eq('category_type', category_type)
    else:
        existing = existing.is_('category_type', 'null')
    existing = existing.execute()
    if existing.data and len(existing.data) > 0:
        return  # Row already exists — purchase will be recomputed live on GET
    # Compute opening from previous day's closing
    prev_q = supabase.table('daily_inventory').select('balance').eq('org_id', org_id)
    prev_q = prev_q.eq('material_name', material_name)
    if category_type:
        prev_q = prev_q.eq('category_type', category_type)
    else:
        prev_q = prev_q.is_('category_type', 'null')
    prev_q = prev_q.lt('entry_date', entry_date).order('entry_date', desc=True).limit(1).execute()
    opening = float(prev_q.data[0]['balance'] or 0) if prev_q.data else 0
    # Compute purchase from inventory_purchases
    purchase = _compute_daily_purchase(org_id, None, material_name, category_type, entry_date)
    new_row = {
        'id': str(__import__('uuid').uuid4()),
        'org_id': org_id,
        'entry_date': entry_date,
        'material_name': material_name,
        'category_type': category_type,
        'opening': opening,
        'usage_qty': 0,
        'created_by': user_email,
    }
    try:
        supabase.table('daily_inventory').upsert(new_row, on_conflict='id').execute()
        logger.info(f'Auto-created daily entry for {material_name} on {entry_date} (purchase={purchase})')
    except Exception as e:
        logger.warning(f'Auto-create daily entry failed (non-fatal): {e}')


def _ensure_vendor(org_id, name):
    """Auto-create vendor if new (case-insensitive, org-scoped). Returns vendor_id."""
    if not supabase or not name:
        return None
    existing = supabase.table('vendors').select('id,data').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
    for r in (existing.data or []):
        if ((r.get('data') or {}).get('name') or '').strip().lower() == name.strip().lower():
            return r['id']
    vid = str(__import__('uuid').uuid4())
    vendor_data = {'id': vid, 'name': name}
    supabase.table('vendors').upsert({'id': vid, 'data': vendor_data, 'name': name, 'org_id': org_id}, on_conflict='id').execute()
    _dual_write_vendor_v2(vendor_data)
    return vid


# ---------- Day Book routes ----------

@app.route('/api/day-book-entries')
@requires_role_or_override('supervisor')
def api_day_book_entries_list():
    if not supabase:
        return jsonify([]), 500
    org_id = session['user'].get('org_id')
    q = supabase.table('inventory_purchases').select('*').eq('org_id', org_id)
    venture_id = request.args.get('venture_id')
    if venture_id and venture_id != '__all__':
        allowed = _allowed_ventures(session['user'])
        if venture_id not in allowed:
            return jsonify({'error': 'Forbidden'}), 403
        q = q.eq('venture_id', venture_id)
    for f in ('vendor_id', 'material_name', 'category', 'category_type'):
        v = request.args.get(f)
        if v and v != 'all':
            q = q.eq(f, v)
    is_gst = request.args.get('is_gst')
    if is_gst == 'true':
        q = q.eq('is_gst', True)
    elif is_gst == 'false':
        q = q.eq('is_gst', False)
    frm, to = request.args.get('from'), request.args.get('to')
    if frm:
        q = q.gte('invoice_date', frm)
    if to:
        q = q.lte('invoice_date', to)
    res = q.order('invoice_date', desc=True).execute()
    return jsonify(res.data or [])


@app.route('/api/day-book', methods=['POST'])
@requires_role_or_override('supervisor')
def api_day_book_upsert():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    org_id = session['user'].get('org_id')
    user_email = (session.get('user') or {}).get('email', '')
    try:
        qty = float(body.get('qty') or 0)
        rate = float(body.get('rate') or 0)
    except (ValueError, TypeError):
        return jsonify({'error': 'qty and rate must be numbers'}), 400
    if body.get('is_gst'):
        if not (body.get('invoice_no') or '').strip():
            return jsonify({'error': 'Invoice number is required when GST is enabled'}), 400
    venture_id = body.get('venture_id')
    if venture_id:
        allowed = _allowed_ventures(session['user'])
        if venture_id not in allowed:
            return jsonify({'error': 'Forbidden venture'}), 403

    payment_type = body.get('payment_type', 'vendor')
    if payment_type not in ('vendor', 'contract', 'inventory', 'other'):
        return jsonify({'error': 'Invalid payment_type. Must be "vendor", "contract", "inventory", or "other".'}), 400

    # --- Contract Payment path ---
    if payment_type == 'contract':
        contract_id = body.get('contract_id')
        contractor_name = (body.get('contractor_name') or '').strip()
        if not contract_id and not contractor_name:
            return jsonify({'error': 'contract_id or contractor_name is required for contract payment type'}), 400
        if not contract_id:
            # Auto-create a new contract for this contractor
            new_contract = {
                'org_id': org_id,
                'person_name': contractor_name,
                'work_description': body.get('material_name') or 'From Day Book',
                'total_amount': qty * rate,
                'total_units': 1,
                'completed_units': 0,
                'unit_label': 'unit',
                'status': 'active',
                'notes': 'Auto-created from Day Book',
                'created_by': user_email,
            }
            try:
                cres = supabase.table('contractor_contracts').insert(new_contract).execute()
                if cres.data:
                    contract_id = cres.data[0]['id']
                else:
                    return jsonify({'error': 'Failed to auto-create contractor contract'}), 500
            except Exception as cc_err:
                logger.error(f'Auto-create contractor contract from Day Book failed: {cc_err}')
                friendly = _map_pg_check_error(cc_err)
                return jsonify({'error': friendly or str(cc_err)}), 400
        contract = _get_org_contract_or_403(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found or does not belong to your org'}), 403
        if contract.get('status') == 'cancelled':
            return jsonify({'error': 'Cannot record payments on a cancelled contract.'}), 400
        # Use contractor name as vendor_name for display in Day Book
        body['vendor_name'] = contract['person_name']
        body['vendor_id'] = None
        body['contract_id'] = contract_id
        body['payment_type'] = 'contract'
        if not body.get('id'):
            body['id'] = str(__import__('uuid').uuid4())
        body['org_id'] = org_id
        body['qty'] = qty
        body['rate'] = rate
        body['amount'] = qty * rate
        body['created_by'] = user_email
        # Upsert into inventory_purchases (Day Book record)
        supabase.table('inventory_purchases').upsert(body, on_conflict='id').execute()
        # Also create a contractor_payments record (linked to contract)
        pay_method = body.get('payment_method', 'cash') or 'cash'
        valid_methods = ('cash', 'upi', 'cheque', 'bank_transfer')
        if pay_method not in valid_methods:
            pay_method = 'cash'
        try:
            supabase.table('contractor_payments').insert({
                'contract_id': contract_id,
                'amount': qty * rate,
                'payment_date': body.get('received_date') or body.get('invoice_date') or now_ist().date().isoformat(),
                'method': pay_method,
                'reference': body.get('invoice_no') or '',
                'notes': (body.get('remarks') or 'From Day Book').strip() or 'From Day Book',
                'recorded_by': user_email,
            }).execute()
        except Exception as cp_err:
            logger.warning(f'Auto-create contractor_payment from Day Book failed (non-fatal): {cp_err}')
        # Audit log
        try:
            supabase.table('audit_log').insert({
                'org_id': org_id, 'user_email': user_email,
                'action': 'day_book_upsert_contract', 'target_id': body['id'],
                'old_data': None, 'new_data': body
            }).execute()
        except Exception as audit_err:
            logger.warning(f'Audit log insert failed (non-fatal): {audit_err}')
        return jsonify({'success': True})

    # --- Other (miscellaneous expense) path ---
    if payment_type == 'other':
        if not body.get('id'):
            body['id'] = str(__import__('uuid').uuid4())
        body['org_id'] = org_id
        body['vendor_id'] = None
        body['vendor_name'] = None
        body['contract_id'] = None
        body['payment_type'] = 'other'
        # For 'other' entries, amount is sent directly (not qty*rate)
        direct_amount = float(body.get('amount') or 0)
        body['qty'] = 1
        body['rate'] = direct_amount
        body['amount'] = direct_amount
        body['created_by'] = user_email
        if not body.get('material_name'):
            body['material_name'] = (body.get('remarks') or 'Other Expense')[:200]
        supabase.table('inventory_purchases').upsert(body, on_conflict='id').execute()
        try:
            supabase.table('audit_log').insert({
                'org_id': org_id, 'user_email': user_email,
                'action': 'day_book_upsert_other', 'target_id': body['id'],
                'old_data': None, 'new_data': body
            }).execute()
        except Exception as audit_err:
            logger.warning(f'Audit log insert failed (non-fatal): {audit_err}')
        return jsonify({'success': True})

    # --- Inventory path (no vendor, routes to stock ledger) ---
    if payment_type == 'inventory':
        if not body.get('id'):
            body['id'] = str(__import__('uuid').uuid4())
        body['org_id'] = org_id
        body['vendor_id'] = None
        body['vendor_name'] = None
        body['contract_id'] = None
        body['payment_type'] = 'inventory'
        body['qty'] = qty
        body['rate'] = rate
        body['amount'] = qty * rate
        body['created_by'] = user_email
        # auto-create material master entry if new
        mname = (body.get('material_name') or '').strip()
        if mname:
            _ensure_material_master(org_id, mname, body.get('unit'))
        supabase.table('inventory_purchases').upsert(body, on_conflict='id').execute()
        try:
            supabase.table('audit_log').insert({
                'org_id': org_id, 'user_email': user_email,
                'action': 'day_book_upsert_inventory', 'target_id': body['id'],
                'old_data': None, 'new_data': body
            }).execute()
        except Exception as audit_err:
            logger.warning(f'Audit log insert failed (non-fatal): {audit_err}')
        # Auto-create stock_ledger IN entry
        received_date = body.get('received_date')
        if mname and received_date and qty > 0:
            try:
                stock_mat = _get_or_create_material_for_stock(mname, venture_id, body.get('category'), body.get('unit'))
                if stock_mat:
                    stock_entry = {
                        'id': str(__import__('uuid').uuid4()),
                        'venture_id': venture_id or 'WAREHOUSE',
                        'material_id': stock_mat['id'],
                        'entry_type': 'IN',
                        'qty': qty,
                        'entry_date': received_date,
                        'vendor_id': None,
                        'rate': rate,
                        'amount': qty * rate,
                        'remarks': (body.get('remarks') or 'From Day Book (Inventory)').strip() or 'From Day Book (Inventory)',
                        'created_by': user_email
                    }
                    supabase.table('stock_ledger').upsert(stock_entry, on_conflict='id').execute()
            except Exception as e:
                logger.warning(f'Auto-create stock ledger from day book inventory failed (non-fatal): {e}')
        # Auto-create daily inventory entry
        if mname and received_date:
            _auto_create_daily_entry(org_id, mname, body.get('category_type'), received_date, user_email)
        return jsonify({'success': True})

    # --- Vendor path (existing logic) ---
    # duplicate-purchase soft check (vendor + invoice_no + invoice_date)
    if not body.get('force') and body.get('invoice_no') and body.get('vendor_id'):
        dup = supabase.table('inventory_purchases').select('id').eq('org_id', org_id)\
            .eq('vendor_id', body['vendor_id']).eq('invoice_no', body['invoice_no'])
        if body.get('invoice_date'):
            dup = dup.eq('invoice_date', body['invoice_date'])
        dup = dup.execute()
        if dup.data and not any(r['id'] == body.get('id') for r in dup.data):
            return jsonify({'error': 'duplicate', 'message': 'A purchase with this invoice number already exists for this vendor. Continue anyway?'}), 409
    if not body.get('id'):
        body['id'] = str(__import__('uuid').uuid4())
    body['org_id'] = org_id
    body['qty'] = qty
    body['rate'] = rate
    body['amount'] = qty * rate
    body['created_by'] = user_email
    body['payment_type'] = 'vendor'
    # auto-create material master entry if new
    mname = (body.get('material_name') or '').strip()
    if mname:
        _ensure_material_master(org_id, mname, body.get('unit'))
    # auto-create vendor if new name typed (org-scoped, case-insensitive)
    vname = (body.get('vendor_name') or '').strip()
    if vname and not body.get('vendor_id'):
        body['vendor_id'] = _ensure_vendor(org_id, vname)
    # audit log: capture old data if editing
    old_data = None
    if body.get('id'):
        try:
            old = supabase.table('inventory_purchases').select('*').eq('id', body['id']).eq('org_id', org_id).execute()
            if old.data:
                old_data = old.data[0]
        except Exception:
            pass
    supabase.table('inventory_purchases').upsert(body, on_conflict='id').execute()
    try:
        supabase.table('audit_log').insert({
            'org_id': org_id, 'user_email': user_email,
            'action': 'day_book_upsert', 'target_id': body['id'],
            'old_data': old_data, 'new_data': body
        }).execute()
    except Exception as audit_err:
        logger.warning(f'Audit log insert failed (non-fatal): {audit_err}')
    # Auto-create daily inventory entry so purchase appears in the register
    received_date = body.get('received_date')
    category_type = body.get('category_type')
    if mname and received_date:
        _auto_create_daily_entry(org_id, mname, category_type, received_date, user_email)
    # Auto-create stock_ledger IN entry so purchase appears in main inventory
    if mname and received_date and qty > 0:
        try:
            stock_mat = _get_or_create_material_for_stock(mname, venture_id, body.get('category'), body.get('unit'))
            if stock_mat:
                stock_entry = {
                    'id': str(__import__('uuid').uuid4()),
                    'venture_id': venture_id or 'WAREHOUSE',
                    'material_id': stock_mat['id'],
                    'entry_type': 'IN',
                    'qty': qty,
                    'entry_date': received_date,
                    'vendor_id': body.get('vendor_id'),
                    'rate': rate,
                    'amount': qty * rate,
                    'remarks': (body.get('remarks') or 'From Day Book').strip() or 'From Day Book',
                    'created_by': user_email
                }
                supabase.table('stock_ledger').upsert(stock_entry, on_conflict='id').execute()
        except Exception as e:
            logger.warning(f'Auto-create stock ledger from day book failed (non-fatal): {e}')
    return jsonify({'success': True})


@app.route('/api/day-book/<pid>', methods=['DELETE'])
@requires_role_or_override('supervisor')
def api_day_book_delete(pid):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    org_id = session['user'].get('org_id')
    user_email = (session.get('user') or {}).get('email', '')
    try:
        old = supabase.table('inventory_purchases').select('*').eq('id', pid).eq('org_id', org_id).execute()
        old_data = old.data[0] if old.data else None
    except Exception:
        old_data = None
    supabase.table('inventory_purchases').delete().eq('id', pid).eq('org_id', org_id).execute()
    try:
        supabase.table('audit_log').insert({
            'org_id': org_id, 'user_email': user_email,
            'action': 'day_book_delete', 'target_id': pid,
            'old_data': old_data, 'new_data': None
        }).execute()
    except Exception as audit_err:
        logger.warning(f'Audit log insert failed (non-fatal): {audit_err}')
    return jsonify({'success': True})


@app.route('/api/day-book/payments')
@requires_role_or_override('supervisor')
def api_day_book_payments():
    if not supabase:
        return jsonify([]), 500
    org_id = session['user'].get('org_id')
    q = supabase.table('inventory_purchase_payments').select('*').eq('org_id', org_id)
    vendor_id = request.args.get('vendor_id')
    if vendor_id:
        q = q.eq('vendor_id', vendor_id)
    res = q.order('payment_date', desc=True).execute()
    return jsonify(res.data or [])


@app.route('/api/day-book/payment', methods=['POST'])
@requires_role_or_override('supervisor')
def api_day_book_payment_post():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    try:
        body['amount'] = float(body.get('amount') or 0)
        if body['amount'] <= 0:
            return jsonify({'error': 'Payment amount must be > 0'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'amount must be a number'}), 400
    body['org_id'] = session['user'].get('org_id')
    body['created_by'] = (session.get('user') or {}).get('email', '')
    if not body.get('id'):
        body['id'] = str(__import__('uuid').uuid4())
    supabase.table('inventory_purchase_payments').upsert(body, on_conflict='id').execute()
    return jsonify({'success': True})


@app.route('/api/day-book/payment/<pid>', methods=['DELETE'])
@requires_role_or_override('supervisor')
def api_day_book_payment_delete(pid):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    org_id = session['user'].get('org_id')
    supabase.table('inventory_purchase_payments').delete().eq('id', pid).eq('org_id', org_id).execute()
    return jsonify({'success': True})


@app.route('/api/day-book/vendor-outstanding')
@requires_role_or_override('supervisor')
def api_day_book_vendor_outstanding():
    """Per-vendor outstanding = total purchased - total paid.
    Accessible by admin, manager, and supervisor."""
    if not supabase:
        return jsonify([]), 500
    org_id = session['user'].get('org_id')
    purchases = supabase.table('inventory_purchases').select('vendor_id,vendor_name,amount').eq('org_id', org_id).execute()
    payments = supabase.table('inventory_purchase_payments').select('vendor_id,vendor_name,amount').eq('org_id', org_id).execute()
    agg = {}
    for r in (purchases.data or []):
        vid = r.get('vendor_id') or ('name:' + (r.get('vendor_name') or ''))
        a = agg.setdefault(vid, {'vendor_id': vid, 'vendor_name': r.get('vendor_name') or '', 'total_purchased': 0, 'total_paid': 0})
        a['total_purchased'] += float(r.get('amount') or 0)
        if not a['vendor_name']:
            a['vendor_name'] = r.get('vendor_name') or ''
    for r in (payments.data or []):
        vid = r.get('vendor_id') or ('name:' + (r.get('vendor_name') or ''))
        a = agg.setdefault(vid, {'vendor_id': vid, 'vendor_name': r.get('vendor_name') or '', 'total_purchased': 0, 'total_paid': 0})
        a['total_paid'] += float(r.get('amount') or 0)
        if not a['vendor_name']:
            a['vendor_name'] = r.get('vendor_name') or ''
    out = []
    for a in agg.values():
        a['outstanding'] = round(a['total_purchased'] - a['total_paid'], 2)
        out.append(a)
    out.sort(key=lambda x: x['vendor_name'].lower())
    return jsonify(out)


@app.route('/api/day-book/vendor/<vendor_id>')
@requires_role_or_override('supervisor')
def api_day_book_vendor_detail(vendor_id):
    """Per-vendor detail: full purchase history + payment history + summary (pending bills)."""
    if not supabase:
        return jsonify({}), 500
    org_id = session['user'].get('org_id')
    purchases = supabase.table('inventory_purchases').select('*').eq('org_id', org_id).eq('vendor_id', vendor_id).order('invoice_date', desc=True).execute()
    payments = supabase.table('inventory_purchase_payments').select('*').eq('org_id', org_id).eq('vendor_id', vendor_id).order('payment_date', desc=True).execute()
    total_purchased = sum(float(r.get('amount') or 0) for r in (purchases.data or []))
    total_paid = sum(float(r.get('amount') or 0) for r in (payments.data or []))
    return jsonify({
        'purchases': purchases.data or [],
        'payments': payments.data or [],
        'summary': {
            'total_purchased': round(total_purchased, 2),
            'total_paid': round(total_paid, 2),
            'outstanding': round(total_purchased - total_paid, 2),
        }
    })


# ---------- Vendor Directory (enriched) ----------

@app.route('/api/vendor-directory')
@requires_role_or_override('supervisor')
def api_vendor_directory():
    """Enriched vendor list with materials, categories, totals, outstanding from Day Book."""
    if not supabase:
        return jsonify([]), 500
    try:
        org_id = session['user'].get('org_id')
        logger.info(f'[vendor-directory] org_id={org_id}')
        # Get all vendors
        vres = supabase.table('vendors').select('id,data,name,org_id').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
        vendors = vres.data or []
        logger.info(f'[vendor-directory] Found {len(vendors)} vendors from DB')
        for v in vendors[:5]:
            logger.info(f'[vendor-directory] vendor: id={v.get("id")}, name={v.get("name")}, data_name={(v.get("data") or {}).get("name")}, org_id={v.get("org_id")}')
        # Get all purchases for this org
        pres = supabase.table('inventory_purchases').select('vendor_id,vendor_name,material_name,category,amount,qty,rate').eq('org_id', org_id).execute()
        purchases = pres.data or []
        # Get all payments for this org
        payres = supabase.table('inventory_purchase_payments').select('vendor_id,amount').eq('org_id', org_id).execute()
        payments = payres.data or []
        # Build aggregates
        purchase_map = {}  # vendor_id -> {total, materials, categories, qty}
        for p in purchases:
            vid = p.get('vendor_id')
            if not vid:
                continue
            if vid not in purchase_map:
                purchase_map[vid] = {'total_purchased': 0, 'materials': set(), 'categories': set(), 'total_qty': 0}
            purchase_map[vid]['total_purchased'] += float(p.get('amount') or 0)
            purchase_map[vid]['total_qty'] += float(p.get('qty') or 0)
            if p.get('material_name'):
                purchase_map[vid]['materials'].add(p['material_name'])
            if p.get('category'):
                purchase_map[vid]['categories'].add(p['category'])
        payment_map = {}
        for p in payments:
            vid = p.get('vendor_id')
            if not vid:
                continue
            payment_map[vid] = payment_map.get(vid, 0) + float(p.get('amount') or 0)
        # Build result
        result = []
        for v in vendors:
            vd = v.get('data') or {}
            vid = v.get('id') or vd.get('id')
            name = vd.get('name') or v.get('name') or ''
            if not name:
                continue
            pm = purchase_map.get(vid, {})
            total_purchased = pm.get('total_purchased', 0)
            total_qty = pm.get('total_qty', 0)
            total_paid = payment_map.get(vid, 0)
            unit_price = round(total_purchased / total_qty, 2) if total_qty > 0 else 0
            result.append({
                'id': vid,
                'name': name,
                'phone': vd.get('phone') or '',
                'gstin': vd.get('gstin') or '',
                'type': vd.get('type') or '',
                'venture_id': vd.get('venture_id') or vd.get('ventureId') or '',
                'materials': sorted(list(pm.get('materials', set()))),
                'categories': sorted(list(pm.get('categories', set()))),
                'total_qty': round(total_qty, 2),
                'unit_price': unit_price,
                'total_purchased': round(total_purchased, 2),
                'total_paid': round(total_paid, 2),
                'outstanding': round(total_purchased - total_paid, 2),
            })
        # Sort by name
        result.sort(key=lambda r: r['name'].lower())
        return jsonify(result)
    except Exception as e:
        logger.error(f'Error in vendor-directory: {e}')
        return jsonify([]), 500

@app.route('/api/inventory-materials')
@requires_role_or_override('supervisor')
def api_inventory_materials():
    if not supabase:
        return jsonify([]), 500
    org_id = session['user'].get('org_id')
    res = supabase.table('inventory_materials').select('*').eq('org_id', org_id).order('name').execute()
    return jsonify(res.data or [])


@app.route('/api/inventory-material', methods=['POST'])
@requires_role_or_override('supervisor')
def api_inventory_material_post():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    org_id = session['user'].get('org_id')
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    existing = supabase.table('inventory_materials').select('id,name').eq('org_id', org_id).execute()
    for r in (existing.data or []):
        if (r.get('name') or '').strip().lower() == name.lower():
            return jsonify({'success': True, 'id': r['id'], 'reused': True})
    if not body.get('id'):
        body['id'] = str(__import__('uuid').uuid4())
    body['org_id'] = org_id
    try:
        supabase.table('inventory_materials').upsert(body, on_conflict='id').execute()
    except Exception as e:
        logger.error(f'inventory_material upsert failed (name={name}): {e}')
        existing = supabase.table('inventory_materials').select('id,name').eq('org_id', org_id).execute()
        for r in (existing.data or []):
            if (r.get('name') or '').strip().lower() == name.lower():
                return jsonify({'success': True, 'id': r['id'], 'reused': True})
        raise
    return jsonify({'success': True, 'id': body['id']})


@app.route('/api/inventory-categories')
@requires_role_or_override('supervisor')
def api_inventory_categories():
    if not supabase:
        return jsonify([]), 500
    org_id = session['user'].get('org_id')
    res = supabase.table('inventory_categories').select('*').eq('org_id', org_id).order('name').execute()
    rows = res.data or []
    # Build full tree: categories → types → subcategories → subtypes
    by_parent = {}
    for r in rows:
        pid = r.get('parent_id')
        by_parent.setdefault(pid, []).append(r)
    def build_children(parent_id):
        children = by_parent.get(parent_id, [])
        for c in children:
            c['children'] = build_children(c['id'])
        return children
    cats = build_children(None)
    return jsonify(cats)


@app.route('/api/inventory-category', methods=['POST'])
@requires_role_or_override('supervisor')
def api_inventory_category_post():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    org_id = session['user'].get('org_id')
    body['org_id'] = org_id
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    parent_id = body.get('parent_id')
    if not body.get('id'):
        existing = supabase.table('inventory_categories').select('id,name,parent_id').eq('org_id', org_id).execute()
        for r in (existing.data or []):
            if (r.get('name') or '').strip().lower() == name.lower() and r.get('parent_id') == parent_id:
                body['id'] = r['id']
                return jsonify({'success': True, 'id': r['id'], 'reused': True})
        body['id'] = str(__import__('uuid').uuid4())
    try:
        supabase.table('inventory_categories').upsert(body, on_conflict='id').execute()
    except Exception as e:
        logger.error(f'inventory_category upsert failed (name={name}, parent={parent_id}): {e}')
        existing = supabase.table('inventory_categories').select('id,name,parent_id').eq('org_id', org_id).execute()
        for r in (existing.data or []):
            if (r.get('name') or '').strip().lower() == name.lower() and r.get('parent_id') == parent_id:
                body['id'] = r['id']
                supabase.table('inventory_categories').upsert(body, on_conflict='id').execute()
                return jsonify({'success': True, 'id': r['id'], 'reused': True})
        raise
    return jsonify({'success': True, 'id': body['id']})


# ---------- Daily Inventory routes ----------

@app.route('/api/daily-inventory')
@requires_role_or_override('supervisor')
def api_daily_inventory_list():
    if not supabase:
        return jsonify([]), 500
    org_id = session['user'].get('org_id')
    q = supabase.table('daily_inventory').select('*').eq('org_id', org_id)
    for f in ('material_name', 'category', 'category_type'):
        v = request.args.get(f)
        if v and v != 'all':
            q = q.eq(f, v)
    frm, to = request.args.get('from'), request.args.get('to')
    if frm:
        q = q.gte('entry_date', frm)
    if to:
        q = q.lte('entry_date', to)
    res = q.order('entry_date', desc=False).order('material_name').execute()
    rows = res.data or []
    # Recompute purchase live from inventory_purchases (single source of truth)
    for r in rows:
        r['purchase'] = _compute_daily_purchase(org_id, None, r.get('material_name'), r.get('category_type'), r.get('entry_date'))
        r['total'] = float(r.get('opening') or 0) + r['purchase']
        r['balance'] = r['total'] - float(r.get('usage_qty') or 0)
    return jsonify(rows)


@app.route('/api/daily-inventory', methods=['POST'])
@requires_role_or_override('supervisor')
def api_daily_inventory_upsert():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    org_id = session['user'].get('org_id')
    try:
        usage_qty = float(body.get('usage_qty') or 0)
    except (ValueError, TypeError):
        return jsonify({'error': 'usage_qty must be a number'}), 400
    venture_id = body.get('venture_id')
    if venture_id:
        allowed = _allowed_ventures(session['user'])
        if venture_id not in allowed:
            return jsonify({'error': 'Forbidden venture'}), 403
    body['org_id'] = org_id
    if not body.get('id'):
        body['id'] = str(__import__('uuid').uuid4())
    # opening: auto-carry from previous date's balance (type-scoped, NULL-safe)
    raw_opening = body.get('opening')
    if raw_opening not in (None, '', 'null'):
        try:
            opening = float(raw_opening)
        except (ValueError, TypeError):
            return jsonify({'error': 'opening must be a number'}), 400
    else:
        prev_q = supabase.table('daily_inventory').select('balance').eq('org_id', org_id)
        category_type = body.get('category_type')
        prev_q = prev_q.eq('material_name', body.get('material_name'))
        if category_type:
            prev_q = prev_q.eq('category_type', category_type)
        else:
            prev_q = prev_q.is_('category_type', 'null')
        prev_q = prev_q.lt('entry_date', body.get('entry_date')).order('entry_date', desc=True).limit(1).execute()
        opening = float(prev_q.data[0]['balance'] or 0) if prev_q.data else 0
    # purchase: auto-compute from inventory_purchases (single source of truth)
    purchase = _compute_daily_purchase(org_id, venture_id, body.get('material_name'), body.get('category_type'), body.get('entry_date'))
    total = opening + purchase
    balance = total - usage_qty
    # negative balance guard
    if balance < 0:
        return jsonify({'error': f'Usage ({usage_qty}) exceeds available stock ({total}). Closing balance cannot be negative.'}), 400
    body['opening'] = opening
    body['purchase'] = purchase
    body['usage_qty'] = usage_qty
    body['total'] = total
    body['balance'] = balance
    body['created_by'] = (session.get('user') or {}).get('email', '')
    supabase.table('daily_inventory').upsert(body, on_conflict='id').execute()
    return jsonify({'success': True, 'opening': opening, 'purchase': purchase, 'total': total, 'balance': balance})


@app.route('/api/daily-inventory/<did>', methods=['DELETE'])
@requires_role_or_override('supervisor')
def api_daily_inventory_delete(did):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    org_id = session['user'].get('org_id')
    # server-side lock: only latest date per group can be deleted
    row = supabase.table('daily_inventory').select('material_name,category_type,entry_date').eq('id', did).eq('org_id', org_id).execute()
    if not row.data:
        return jsonify({'error': 'Not found'}), 404
    r = row.data[0]
    newer = supabase.table('daily_inventory').select('id').eq('org_id', org_id)
    newer = newer.eq('material_name', r['material_name'])
    if r.get('category_type'):
        newer = newer.eq('category_type', r['category_type'])
    else:
        newer = newer.is_('category_type', 'null')
    newer = newer.gt('entry_date', r['entry_date']).limit(1).execute()
    if newer.data:
        return jsonify({'error': 'Cannot delete a past row — later entries depend on its balance. Delete the latest entry first.'}), 409
    supabase.table('daily_inventory').delete().eq('id', did).eq('org_id', org_id).execute()
    return jsonify({'success': True})


# ========================
# Cells Reorder API
# ========================

@app.route('/api/cells/reorder', methods=['POST'])
@requires_role_or_override('supervisor')
def api_cells_reorder():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    body = request.get_json() or {}
    venture_id = body.get('venture_id')
    work_item = body.get('work_item')
    ordered_ids = body.get('ordered_ids', [])
    if not ordered_ids:
        return jsonify({'success': True})
    try:
        for idx, cid in enumerate(ordered_ids):
            supabase.table('category_sets').update({
                'sort_order': idx
            }).eq('venture_id', venture_id).eq('name', work_item).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error reordering cells: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Category Creation API
# ========================

@app.route('/api/category', methods=['POST'])
@requires_role_or_override('supervisor')
def api_category_create():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    body = request.get_json() or {}
    venture_id = body.get('venture_id')
    name = body.get('name', '').strip()
    if not name or not venture_id:
        return jsonify({'error': 'venture_id and name are required'}), 400
    try:
        # Get org_id from venture
        vres = supabase.table('ventures').select('*').eq('id', venture_id).execute()
        org_id = None
        if vres.data:
            org_id = (vres.data[0].get('data') or {}).get('org_id')
        # Get max sort_order
        existing = supabase.table('category_sets').select('sort_order').eq(
            'venture_id', venture_id).eq('category_type', 'work_group').order(
            'sort_order', desc=True).limit(1).execute()
        next_order = (existing.data[0]['sort_order'] + 1) if existing.data else 0
        res = supabase.table('category_sets').insert({
            'org_id': org_id or '11111111-1111-1111-1111-111111111111',
            'venture_id': venture_id,
            'category_type': 'work_group',
            'name': name,
            'sort_order': next_order
        }).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error creating category: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Instant Reports API (Admin/Manager/Supervisor)
# ========================

def _slug_id(text):
    """Python equivalent of JS slugId function.
    JS: text.toLowerCase().replace(/[^a-z0-9]/g, '_').substring(0, 30)"""
    import re
    s = text.lower()
    s = re.sub(r'[^a-z0-9]', '_', s)
    return s[:30]


def _ensure_item_ids(items):
    """Python equivalent of JS ensureItemIds function.
    If items are strings, generate {id, label} objects matching frontend logic.
    If items are already objects with 'id', return as-is."""
    if not items:
        return []
    result = []
    for i, item in enumerate(items):
        if isinstance(item, dict) and item.get('id'):
            result.append(item)
        elif isinstance(item, str):
            result.append({
                'id': f'item_{_slug_id(item)}_{i}',
                'label': item
            })
    return result


DEFAULT_WORK_CATEGORIES = {
    'CIVIL WORK': [
        "Brick work", "Lintel", "Lanter", "Mesh", "Mesh & Brickwork NCC",
        "Connections", "Lift", "Cupboards", "Red Oxide Duraplus Primer",
        "Red Oxide Duraplus Primer (2nd coat)", "Bathroom Service Chargable"
    ],
    'ELECTRICAL & PLUMBING WORK': [
        "Electrical pipe", "Pipe & GI box", "Wiring",
        "Bathroom Chipped", "Bathroom Geyser Pipe",
        "Bathroom Geyser & Pipes", "Sanitary Board & Nand",
        "GC & Bath Fitting"
    ],
    'POP CEILING': [
        "Pop bolster work", "Pop ready work", "Casing",
        "Balloon PVC Box Fitting", "Connections / Measurement"
    ],
    'PAINTING': [
        "Colour Primer", "Wall Care Plaster",
        "Wall Care Slastoat", "Wall Primer", "Primer",
        "Colour to Edge"
    ],
    'FLOORING': [
        "Bathroom Wall Tiles", "Tile Laying",
        "Tile Cutting", "Connections", "Window Dhanis",
        "Colour to Edge", "Wedding Dhanis"
    ],
    'CORRIDORS': [
        {'id': 'corridor_0', 'label': 'Plaster'},
        {'id': 'corridor_1', 'label': 'Mesh'},
        {'id': 'corridor_2', 'label': 'Lanter'},
        {'id': 'corridor_3', 'label': 'Wiring'},
        {'id': 'corridor_4', 'label': 'Stains & Cleaning'},
        {'id': 'corridor_5', 'label': 'Flooring'}
    ],
    'ELEVATION WORK': [
        {'id': 'elevation_0', 'label': 'Marka'},
        {'id': 'elevation_1', 'label': 'Elevation'},
        {'id': 'elevation_2', 'label': 'Electrics'},
        {'id': 'elevation_3', 'label': 'Wall Care'},
        {'id': 'elevation_4', 'label': 'Texture'}
    ]
}


def _ensure_work_categories(cats):
    """Python equivalent of JS ensureWorkCategories function.
    Falls back to DEFAULT_WORK_CATEGORIES when cats is empty, matching frontend behavior.
    Generates item IDs matching frontend logic for string items."""
    if not cats or (isinstance(cats, dict) and len(cats) == 0):
        cats = DEFAULT_WORK_CATEGORIES
    result = {}
    for cat_label, items in cats.items():
        if not items:
            result[cat_label] = []
            continue
        if isinstance(items[0], dict) and items[0].get('id'):
            result[cat_label] = items
        else:
            result[cat_label] = [
                {'id': f'item_{_slug_id(cat_label)}_{_slug_id(label)}_{i}', 'label': label}
                for i, label in enumerate(items)
                if isinstance(label, str)
            ]
    return result


def _parse_cell_id(cell_id, venture_id):
    """Parse a cell ID to extract block, floor, flat, work_item.
    Supports three formats:
    1. Flat view: {venture}_{block}_floor{N}_{flatNum}_{itemId}
       e.g., elite_A_floor1_101_item_brick_work_0
    2. Work view: {venture}_{block}_floor{N}_{categorySlug}_{itemId}_{flatNum}
       e.g., elite_A_floor1_civil_work_item_civil_work_brick_work_0_101
    3. Super structure: {venture}_superstructure_{block}_{itemId}
       e.g., elite_superstructure_A_item_pile_caps_5
    Returns dict with block, floor, flat, item_id or None if parsing fails."""
    import re
    prefix = venture_id + '_'
    if not cell_id.startswith(prefix):
        return None
    rest = cell_id[len(prefix):]

    # Format 3: superstructure_{block}_{itemId}
    if rest.startswith('superstructure_'):
        ss_rest = rest[len('superstructure_'):]
        m = re.match(r'^([^_]+)_(.+)$', ss_rest)
        if m:
            return {
                'block': m.group(1),
                'floor': None,
                'flat': None,
                'item_id': m.group(2)
            }
        return None

    # Format 1: {block}_floor{N}_{flatNum}_{itemId}
    # flatNum is typically 3 digits (e.g., 101, 102) or P-XXX (parking/common)
    m = re.match(r'^(.+)_floor(\d+)_(\d{3}|P-\d{3})_(.+)$', rest)
    if m:
        flat_str = m.group(3)
        flat_val = int(flat_str) if flat_str.isdigit() else flat_str
        return {
            'block': m.group(1),
            'floor': int(m.group(2)),
            'flat': flat_val,
            'item_id': m.group(4)
        }

    # Format 2: {block}_floor{N}_{categorySlug}_{itemId}_{flatNum}
    # itemId starts with 'item_' or a known prefix, flatNum is 3 digits or P-XXX at end
    m = re.match(r'^(.+)_floor(\d+)_(.+?)_(item_.+)_(\d{3}|P-\d{3})$', rest)
    if m:
        flat_str = m.group(5)
        flat_val = int(flat_str) if flat_str.isdigit() else flat_str
        return {
            'block': m.group(1),
            'floor': int(m.group(2)),
            'flat': flat_val,
            'item_id': m.group(4),
            'category_slug': m.group(3)
        }

    # Format 2 variant: non-item_ prefixed IDs (e.g., corridor_0, elevation_0)
    m = re.match(r'^(.+)_floor(\d+)_(.+?)_([^_]+_\d+)_(\d{3}|P-\d{3})$', rest)
    if m:
        flat_str = m.group(5)
        flat_val = int(flat_str) if flat_str.isdigit() else flat_str
        return {
            'block': m.group(1),
            'floor': int(m.group(2)),
            'flat': flat_val,
            'item_id': m.group(4),
            'category_slug': m.group(3)
        }

    return None


@app.route('/api/reports/instant')
@requires_role('supervisor', 'manager', 'admin')
def api_instant_reports():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    venture_id = request.args.get('venture_id')
    if not venture_id:
        return jsonify({'error': 'venture_id is required'}), 400
    # Optional filters
    filter_block = request.args.get('block', '')
    filter_floor = request.args.get('floor', '')
    filter_flat = request.args.get('flat', '')
    filter_category = request.args.get('category', '')
    filter_date_from = request.args.get('date_from', '')
    filter_date_to = request.args.get('date_to', '')
    try:
        # Fetch venture data for work_categories and flat_view_items
        vent_res = supabase.table('ventures').select('*').eq('id', venture_id).execute()
        venture_data = {}
        if vent_res.data:
            venture_data = vent_res.data[0].get('data') or {}
        flat_view_items_raw = venture_data.get('flat_view_items', [])
        work_categories_raw = venture_data.get('work_categories', {})
        blocks = venture_data.get('blocks', [])

        # Normalize items using same logic as frontend
        flat_view_items = _ensure_item_ids(flat_view_items_raw)
        work_categories = _ensure_work_categories(work_categories_raw)
        super_structure_items = _ensure_item_ids(venture_data.get('super_structure_items', []))

        # Build item_id -> category mapping and item_id -> label mapping
        item_to_category = {}
        item_id_to_label = {}
        # Track all configured categories and their items (for complete hierarchy)
        all_configured_categories = {}  # cat_label -> list of {id, label}

        # Map work_categories items
        for cat_label, items in work_categories.items():
            all_configured_categories[cat_label] = []
            for item in items:
                if isinstance(item, dict):
                    iid = item.get('id', '')
                    if iid:
                        item_to_category[iid] = cat_label
                        item_id_to_label[iid] = item.get('label', iid)
                        all_configured_categories[cat_label].append(item)

        # Map flat_view_items to "Flat View" category if not already in work_categories
        for item in flat_view_items:
            if isinstance(item, dict):
                iid = item.get('id', '')
                if iid and iid not in item_to_category:
                    item_to_category[iid] = 'Flat View'
                if iid:
                    item_id_to_label[iid] = item.get('label', iid)

        # Fetch cell_data filtered by venture at DB level for accuracy + performance
        query = supabase.table('cell_data').select('*').filter('data->>venture_id', 'eq', venture_id)
        # Paginate to overcome Supabase's default 1000-row limit
        all_cell_rows = []
        page_size = 1000
        offset = 0
        while True:
            res = query.range(offset, offset + page_size - 1).execute()
            all_cell_rows.extend(res.data)
            if len(res.data) < page_size:
                break
            offset += page_size
        status_counts = {'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
        total_cells = 0
        work_item_stats = {}
        category_stats = {}
        block_stats = {}
        floor_stats = {}
        detail_rows = []
        # Track actual work_categories cells per block/floor (excluding flat_view_items)
        block_actual_wc = {}
        floor_actual_wc = {}

        # Pre-initialize category_stats and work_item_stats with ALL configured categories
        # so that categories with no cell_data rows still appear in the report
        for cat_label, cat_items in all_configured_categories.items():
            if filter_category and cat_label != filter_category:
                continue
            category_stats[cat_label] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
            for item in cat_items:
                iid = item.get('id', '')
                if iid:
                    work_item_stats[iid] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
        for row in all_cell_rows:
            d = row.get('data') or {}
            cell_id = row.get('id', '')
            color = d.get('color', '') or 'none'
            updated_at = d.get('updated_at', '')

            # Parse cell ID to get block/floor/flat/item
            parsed = _parse_cell_id(cell_id, venture_id)
            if parsed:
                block = parsed['block']
                floor = parsed['floor']
                flat = parsed['flat']
                item_id = parsed['item_id']
            else:
                # Fallback to data fields
                block = d.get('block', '')
                floor = d.get('floor', '')
                flat = d.get('flat', '')
                item_id = d.get('work_item', d.get('item_id', ''))
                if not block:
                    continue

            # Apply filters
            if filter_block and block != filter_block:
                continue
            if filter_floor and str(floor) != str(filter_floor):
                continue
            if filter_flat and str(flat) != str(filter_flat):
                continue
            cat = item_to_category.get(item_id, '')
            if filter_category and cat != filter_category:
                continue
            if filter_date_from and updated_at:
                cell_date = updated_at[:10]
                if cell_date < filter_date_from:
                    continue
            if filter_date_to and updated_at:
                cell_date = updated_at[:10]
                if cell_date > filter_date_to:
                    continue

            # Count statuses
            if color not in status_counts:
                status_counts[color] = 0
            status_counts[color] += 1
            total_cells += 1

            # Work item stats
            if item_id not in work_item_stats:
                work_item_stats[item_id] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
            work_item_stats[item_id]['total'] += 1
            work_item_stats[item_id][color] = (work_item_stats[item_id].get(color, 0) or 0) + 1

            # Category stats
            if cat:
                if cat not in category_stats:
                    category_stats[cat] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
                category_stats[cat]['total'] += 1
                category_stats[cat][color] = (category_stats[cat].get(color, 0) or 0) + 1

            # Block stats
            if block not in block_stats:
                block_stats[block] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
            block_stats[block]['total'] += 1
            block_stats[block][color] = (block_stats[block].get(color, 0) or 0) + 1

            # Floor stats
            fkey = f'{block}|{floor}'
            if fkey not in floor_stats:
                floor_stats[fkey] = {'block': block, 'floor': floor, 'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
            floor_stats[fkey]['total'] += 1
            floor_stats[fkey][color] = (floor_stats[fkey].get(color, 0) or 0) + 1

            # Track work_categories cells per block/floor (for missing cells calculation)
            if cat and cat in all_configured_categories:
                block_actual_wc[block] = block_actual_wc.get(block, 0) + 1
                floor_actual_wc[fkey] = floor_actual_wc.get(fkey, 0) + 1

            # Detail row
            detail_rows.append({
                'block': block, 'floor': floor, 'flat': flat,
                'work_item': item_id_to_label.get(item_id, item_id),
                'work_item_id': item_id,
                'category': cat,
                'color': color, 'updated_at': updated_at,
                'remarks': d.get('remarks', ''),
                'updated_by': d.get('updated_by', '')
            })

        completed = status_counts.get('green', 0)
        in_progress = status_counts.get('yellow', 0)
        patch_work = status_counts.get('blue', 0)
        yet_to_start = status_counts.get('red', 0)
        not_started = status_counts.get('none', 0)

        # Compute expected cells per work item from venture structure.
        # Cells default to red ("yet to start") — untouched cells have no DB row
        # but should be counted as red in the report for accurate totals.
        # This distributes missing cells to per-item, per-category, per-block,
        # and per-floor stats so newly added categories/items appear with
        # correct counts and all summaries add up to the grand total.
        # Must match frontend CATEGORY_FLATS mapping.
        CATEGORY_FLATS = {
            'CORRIDORS': ['P-004'],
            'ELEVATION WORK': ['P-004'],
        }

        # Helper: compute expected cell count for a given category in a block
        def _expected_for_block(cat_label, blk, cat_special_flats):
            blk_floors = blk.get('floors', 5)
            blk_flats = blk.get('flats_per_floor', 4)
            floors_to_count = 1 if filter_floor else blk_floors
            if cat_special_flats is not None:
                if filter_flat:
                    flats_to_count = 1
                else:
                    flats_to_count = min(len(cat_special_flats), blk_flats)
            else:
                flats_to_count = 1 if filter_flat else blk_flats
            return floors_to_count * flats_to_count, floors_to_count, flats_to_count

        if blocks and isinstance(blocks, list):
            # Pass 1: per-item and per-category missing cells
            # Also track per-block expected totals for Pass 2
            block_expected_totals = {}  # blk_id -> total expected cells across all items
            block_floor_map = {}  # blk_id -> (floors_to_count, flats_to_count) for floor distribution
            for cat_label, cat_items in all_configured_categories.items():
                if filter_category and cat_label != filter_category:
                    continue
                cat_special_flats = CATEGORY_FLATS.get(cat_label)
                # When filtering by a specific flat, skip categories that use
                # special flats (e.g. CORRIDORS uses 'P-004') unless the filter
                # matches one of those special flats.
                if filter_flat and cat_special_flats:
                    if str(filter_flat) not in [str(f) for f in cat_special_flats]:
                        continue
                for item in cat_items:
                    iid = item.get('id', '')
                    if not iid:
                        continue
                    expected = 0
                    for blk in blocks:
                        if not isinstance(blk, dict):
                            continue
                        blk_id = blk.get('id', blk.get('name', ''))
                        if filter_block and blk_id != filter_block:
                            continue
                        block_expected, floors_cnt, flats_cnt = _expected_for_block(cat_label, blk, cat_special_flats)
                        expected += block_expected
                        if block_expected > 0:
                            block_expected_totals[blk_id] = block_expected_totals.get(blk_id, 0) + block_expected
                            block_floor_map[blk_id] = blk  # keep ref for floor count
                    actual = work_item_stats.get(iid, {}).get('total', 0)
                    missing = expected - actual
                    if missing > 0:
                        if iid not in work_item_stats:
                            work_item_stats[iid] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
                        work_item_stats[iid]['total'] += missing
                        work_item_stats[iid]['red'] = (work_item_stats[iid].get('red', 0) or 0) + missing
                        if cat_label not in category_stats:
                            category_stats[cat_label] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
                        category_stats[cat_label]['total'] += missing
                        category_stats[cat_label]['red'] = (category_stats[cat_label].get('red', 0) or 0) + missing
                        total_cells += missing
                        yet_to_start += missing
                        status_counts['red'] = yet_to_start

            # Pass 2: per-block and per-floor missing cells
            for blk_id, expected_total in block_expected_totals.items():
                actual_wc = block_actual_wc.get(blk_id, 0)
                block_missing = expected_total - actual_wc
                if block_missing <= 0:
                    continue
                if blk_id not in block_stats:
                    block_stats[blk_id] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
                block_stats[blk_id]['total'] += block_missing
                block_stats[blk_id]['red'] = (block_stats[blk_id].get('red', 0) or 0) + block_missing
                # Distribute to floor stats
                blk_obj = block_floor_map.get(blk_id)
                num_floors = blk_obj.get('floors', 5) if blk_obj else 5
                if filter_floor:
                    floors_to_iter = [int(filter_floor)]
                else:
                    floors_to_iter = list(range(1, num_floors + 1))
                per_floor = block_missing // len(floors_to_iter) if floors_to_iter else 0
                remainder = block_missing - (per_floor * len(floors_to_iter))
                for idx, fl in enumerate(floors_to_iter):
                    fkey = f'{blk_id}|{fl}'
                    if fkey not in floor_stats:
                        floor_stats[fkey] = {'block': blk_id, 'floor': fl, 'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
                    add = per_floor + (1 if idx < remainder else 0)
                    if add > 0:
                        floor_stats[fkey]['total'] += add
                        floor_stats[fkey]['red'] = (floor_stats[fkey].get('red', 0) or 0) + add

        pending = total_cells - completed
        completion_pct = round((completed / total_cells * 100), 1) if total_cells else 0

        # Build work view hierarchy (Category -> Work Descriptions)
        # Mirrors the Work View module structure exactly
        work_view_hierarchy = []
        for cat_label, cat_items in all_configured_categories.items():
            if filter_category and cat_label != filter_category:
                continue
            cat_stats = category_stats.get(cat_label, {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0})
            cat_total = cat_stats['total']
            cat_completed = cat_stats.get('green', 0)
            item_list = []
            for item in cat_items:
                iid = item.get('id', '')
                stats = work_item_stats.get(iid, {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0})
                t = stats['total']
                item_list.append({
                    'work_item': item.get('label', iid),
                    'work_item_id': iid,
                    'total': t,
                    'completed': stats.get('green', 0),
                    'in_progress': stats.get('yellow', 0),
                    'patch_work': stats.get('blue', 0),
                    'yet_to_start': stats.get('red', 0),
                    'not_started': stats.get('none', 0),
                    'pending': t - stats.get('green', 0),
                    'pct': round((stats.get('green', 0) / t * 100), 1) if t else 0
                })
            work_view_hierarchy.append({
                'category': cat_label,
                'total': cat_total,
                'completed': cat_completed,
                'in_progress': cat_stats.get('yellow', 0),
                'patch_work': cat_stats.get('blue', 0),
                'yet_to_start': cat_stats.get('red', 0),
                'not_started': cat_stats.get('none', 0),
                'pct': round((cat_completed / cat_total * 100), 1) if cat_total else 0,
                'items': item_list
            })
        # Build category summary
        category_summary = []
        for cat, stats in sorted(category_stats.items()):
            t = stats['total']
            category_summary.append({
                'category': cat,
                'total': t,
                'completed': stats.get('green', 0),
                'in_progress': stats.get('yellow', 0),
                'patch_work': stats.get('blue', 0),
                'yet_to_start': stats.get('red', 0),
                'not_started': stats.get('none', 0),
                'pct': round((stats.get('green', 0) / t * 100), 1) if t else 0
            })

        # Build block summary
        block_summary = []
        for blk, stats in sorted(block_stats.items()):
            t = stats['total']
            block_summary.append({
                'block': blk,
                'total': t,
                'completed': stats.get('green', 0),
                'in_progress': stats.get('yellow', 0),
                'patch_work': stats.get('blue', 0),
                'yet_to_start': stats.get('red', 0),
                'not_started': stats.get('none', 0),
                'pct': round((stats.get('green', 0) / t * 100), 1) if t else 0
            })

        # Build floor summary
        floor_summary = []
        for fkey, stats in sorted(floor_stats.items()):
            t = stats['total']
            floor_summary.append({
                'block': stats['block'], 'floor': stats['floor'],
                'total': t,
                'completed': stats.get('green', 0),
                'in_progress': stats.get('yellow', 0),
                'patch_work': stats.get('blue', 0),
                'yet_to_start': stats.get('red', 0),
                'not_started': stats.get('none', 0),
                'pct': round((stats.get('green', 0) / t * 100), 1) if t else 0
            })

        # Spend from invoices
        inv_res = supabase.table('invoices').select('*').execute()
        total_invoice = 0
        for inv in (inv_res.data or []):
            d = inv.get('data') or {}
            if d.get('venture_id') == venture_id or inv.get('venture_id') == venture_id:
                amt = d.get('amount') or inv.get('amount') or 0
                total_invoice += float(amt)

        # Spend from POs
        po_res = supabase.table('purchase_orders').select('*').execute()
        total_po = 0
        for po in (po_res.data or []):
            d = po.get('data') or {}
            if d.get('venture_id') == venture_id or po.get('venture_id') == venture_id:
                amt = d.get('billAmount') or d.get('quotedAmount') or po.get('amount') or 0
                total_po += float(amt)

        # Consumption from stock_ledger
        stock_res = supabase.table('stock_ledger').select('*').eq('venture_id', venture_id).execute()
        consumption = {}
        for entry in (stock_res.data or []):
            if entry.get('entry_type') == 'OUT':
                mid = entry.get('material_id', 'unknown')
                if mid not in consumption:
                    consumption[mid] = {'material_id': mid, 'total_qty': 0}
                consumption[mid]['total_qty'] += float(entry.get('qty', 0))

        result = {
            'venture_id': venture_id,
            'filters': {
                'block': filter_block, 'floor': filter_floor,
                'flat': filter_flat, 'category': filter_category,
                'date_from': filter_date_from, 'date_to': filter_date_to
            },
            'summary': {
                'total_work_items': len(work_item_stats),
                'total_cells': total_cells,
                'completed': completed,
                'in_progress': in_progress,
                'patch_work': patch_work,
                'yet_to_start': yet_to_start,
                'not_started': not_started,
                'pending': pending,
                'completion_pct': completion_pct
            },
            'status_counts': status_counts,
            'work_view_hierarchy': work_view_hierarchy,
            'category_summary': category_summary,
            'block_summary': block_summary,
            'floor_summary': floor_summary,
            'detail_rows': detail_rows,
            'spend': {
                'invoices': round(total_invoice, 2),
                'purchase_orders': round(total_po, 2)
            },
            'consumption': list(consumption.values()),
            'blocks': [{'id': b.get('id', ''), 'name': b.get('name', b.get('id', ''))} for b in blocks],
            'available_categories': sorted(set(item_to_category.values())),
            'available_blocks': sorted(block_stats.keys()),
            'item_labels': item_id_to_label
        }
        return jsonify(result)
    except Exception as e:
        logger.error(f'Error generating instant reports: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Lender Progress Report PDF
# ========================

def _lender_color_to_pct(color):
    """Map cell color to completion percentage for lender reports."""
    return {'green': 100, 'blue': 75, 'yellow': 40, 'red': 0}.get(color, 0)


def _lender_compute_progress(venture_id):
    """Compute % completion per block/floor from cell_data colors."""
    if not supabase:
        return {'blocks': [], 'overall_pct': 0, 'total_cells': 0}
    try:
        res = supabase.table('cell_data').select('*').execute()
        block_stats = {}
        total_weighted = 0
        total_cells = 0
        for row in (res.data or []):
            d = row.get('data') or {}
            if d.get('venture_id') != venture_id:
                continue
            block = d.get('block', 'Unknown')
            floor = d.get('floor', 'Unknown')
            color = d.get('color', 'red')
            pct = _lender_color_to_pct(color)
            key = block
            if key not in block_stats:
                block_stats[key] = {'block': block, 'floors': {}, 'total_pct': 0, 'cell_count': 0}
            floor_key = floor
            if floor_key not in block_stats[key]['floors']:
                block_stats[key]['floors'][floor_key] = {'floor': floor, 'total_pct': 0, 'cell_count': 0}
            block_stats[key]['floors'][floor_key]['total_pct'] += pct
            block_stats[key]['floors'][floor_key]['cell_count'] += 1
            block_stats[key]['total_pct'] += pct
            block_stats[key]['cell_count'] += 1
            total_weighted += pct
            total_cells += 1
        blocks = []
        for block_name, stats in sorted(block_stats.items()):
            block_pct = round(stats['total_pct'] / stats['cell_count'], 1) if stats['cell_count'] else 0
            floors = []
            for floor_name, fs in sorted(stats['floors'].items()):
                floor_pct = round(fs['total_pct'] / fs['cell_count'], 1) if fs['cell_count'] else 0
                floors.append({
                    'floor': fs['floor'],
                    'cell_count': fs['cell_count'],
                    'pct_complete': floor_pct
                })
            blocks.append({
                'block': block_name,
                'cell_count': stats['cell_count'],
                'pct_complete': block_pct,
                'floors': floors
            })
        overall = round(total_weighted / total_cells, 1) if total_cells else 0
        return {'blocks': blocks, 'overall_pct': overall, 'total_cells': total_cells}
    except Exception as e:
        logger.error(f'Error computing lender report progress: {e}')
        return {'blocks': [], 'overall_pct': 0, 'total_cells': 0}


def _lender_compute_financials(venture_id):
    """Compute funds collected and utilized for the report."""
    collected = 0.0
    utilized = 0.0
    if not supabase:
        return {'collected': 0, 'utilized': 0, 'escrow_balance': 0}
    try:
        inv_res = supabase.table('invoices').select('*').execute()
        for inv in inv_res.data or []:
            d = inv.get('data') or {}
            v_match = d.get('venture_id') == venture_id or inv.get('venture_id') == venture_id
            if not v_match:
                continue
            status = (d.get('status') or inv.get('status') or '').lower()
            amt = float(d.get('amount') or inv.get('amount') or 0)
            if status in ('paid', 'received', 'completed'):
                collected += amt
    except Exception as e:
        logger.error(f'Error fetching invoices for lender report: {e}')
    try:
        exp_res = supabase.table('expenditures').select('*').eq('venture_id', venture_id).execute()
        for exp in exp_res.data or []:
            d = exp.get('data') or {}
            utilized += float(d.get('amount', 0))
    except Exception as e:
        logger.error(f'Error fetching expenditures for lender report: {e}')
    return {
        'collected': round(collected, 2),
        'utilized': round(utilized, 2),
        'escrow_balance': round(collected - utilized, 2)
    }


def _lender_latest_photos(venture_id):
    """Return most recent dated photo per block/floor from cell_data remarkImages."""
    photos = []
    if not supabase:
        return photos
    try:
        res = supabase.table('cell_data').select('*').execute()
        seen = {}
        for row in (res.data or []):
            d = row.get('data') or {}
            if d.get('venture_id') != venture_id:
                continue
            block = d.get('block', 'Unknown')
            floor = d.get('floor', 'Unknown')
            key = (block, floor)
            images = d.get('remarkImages') or []
            timeline = d.get('timeline') or []
            for img in images:
                # Try to find a capture date from timeline entries or updated_at
                capture_date = d.get('updated_at', '')[:10]
                for entry in timeline:
                    if entry.get('remarks') and img.get('name') in (entry.get('remarks') or ''):
                        capture_date = entry.get('date', capture_date)[:10]
                        break
                if key not in seen or capture_date > seen[key].get('date', ''):
                    seen[key] = {
                        'block': block,
                        'floor': floor,
                        'src': img.get('dataUrl', ''),
                        'date': capture_date
                    }
        photos = [seen[k] for k in sorted(seen.keys()) if seen[k].get('src')]
    except Exception as e:
        logger.error(f'Error fetching lender report photos: {e}')
    return photos


def _format_inr(num):
    """Format a number in Indian Rupee crore/lakh notation."""
    num = float(num)
    if num >= 10000000:
        return f"\u20b9 {round(num / 10000000, 2)} Cr"
    if num >= 100000:
        return f"\u20b9 {round(num / 100000, 2)} L"
    return f"\u20b9 {round(num, 2)}"


@app.route('/api/reports/lender-report/<project_id>')
@requires_role_or_override('manager', 'admin')
def api_lender_report(project_id):
    """Generate a printable Lender Progress Report PDF."""
    if not HTML and not _pisa:
        return jsonify({'error': 'PDF engine not installed. Run: pip install weasyprint xhtml2pdf'}), 500

    report_date_str = request.args.get('date') or now_ist().strftime('%Y-%m-%d')
    include_financials = request.args.get('include_financials', 'true').lower() != 'false'

    # Venture / project details
    venture = {'id': project_id, 'name': project_id, 'address': '', 'rera_registration': ''}
    prepared_by = ''
    if supabase:
        try:
            vres = supabase.table('ventures').select('*').eq('id', project_id).execute()
            if vres.data:
                vdata = vres.data[0].get('data') or {}
                venture = {
                    'id': project_id,
                    'name': vdata.get('name') or vres.data[0].get('name') or project_id,
                    'address': vdata.get('address', ''),
                    'rera_registration': vdata.get('rera_registration', '')
                }
                # Try to fetch builder name from organization
                org_id = vdata.get('org_id') or vres.data[0].get('org_id')
                if org_id:
                    try:
                        ores = supabase.table('organizations').select('name').eq('id', org_id).single().execute()
                        if ores.data:
                            prepared_by = ores.data.get('name', '')
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f'Error fetching venture for lender report: {e}')

    progress = _lender_compute_progress(project_id)
    financials = _lender_compute_financials(project_id) if include_financials else None
    photos = _lender_latest_photos(project_id)

    ref_id = f"LPR-{project_id.upper()}-{report_date_str.replace('-', '')}"

    rendered = render_template(
        'lender_report.html',
        venture=venture,
        report_date=report_date_str,
        prepared_by=prepared_by or 'VGrand Infra Pvt. Ltd.',
        overall_pct=progress['overall_pct'],
        total_cells=progress['total_cells'],
        blocks=progress['blocks'],
        photos=photos,
        financials=financials,
        include_financials=include_financials,
        ref_id=ref_id
    )

    pdf = render_pdf(rendered)
    filename = f"Lender_Progress_Report_{venture['name'].replace(' ', '_')}_{report_date_str}.pdf"
    response = app.make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ========================
# Payroll API (Admin-only for release; Manager can view)
# ========================

@app.route('/api/payroll')
@requires_role_or_override('supervisor')
def api_payroll_list():
    if not supabase:
        return jsonify([]), 500
    venture_id = request.args.get('venture_id')
    q = supabase.table('payroll').select('*')
    if venture_id:
        q = q.eq('venture_id', venture_id)
    res = q.execute()
    return jsonify(res.data or [])


@app.route('/api/payroll', methods=['POST'])
@requires_role('admin')
def api_payroll_create():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    user = session.get('user', {})
    try:
        row = {
            'venture_id': body.get('venture_id'),
            'subcontractor_id': body.get('subcontractor_id'),
            'amount': body.get('amount', 0),
            'status': 'pending',
            'created_by': user.get('id') if isinstance(user, dict) else None,
        }
        res = supabase.table('payroll').insert(row).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error creating payroll: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/payroll/<payroll_id>/release', methods=['POST'])
@requires_role('admin')
def api_payroll_release(payroll_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        supabase.table('payroll').update({
            'status': 'unlocked',
            'updated_at': datetime.utcnow().isoformat()
        }).eq('id', payroll_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        err_msg = str(e)
        # Surface the Postgres trigger's exception message directly
        if 'Cannot unlock payroll' in err_msg:
            return jsonify({'error': err_msg}), 400
        logger.error(f'Error releasing payroll {payroll_id}: {e}')
        return jsonify({'error': err_msg}), 500


# ========================
# Inventory Audit API (Admin-only)
# ========================

@app.route('/api/inventory/audit')
@requires_role('admin')
def api_inventory_audit():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    venture_id = request.args.get('venture_id')
    if not venture_id:
        return jsonify({'error': 'venture_id is required'}), 400
    try:
        # Get all materials for this venture
        mats_res = supabase.table('materials').select('*').or_(f'venture_id.eq.{venture_id},venture_id.is.null').execute()
        materials = mats_res.data or []
        # Get stock balances
        bal_res = supabase.table('stock_balance').select('*').eq('venture_id', venture_id).execute()
        balances = {b['material_id']: b for b in (bal_res.data or [])}
        # Get PO line items
        po_li_res = supabase.table('po_line_items').select('*').eq('venture_id', venture_id).execute()
        po_items = po_li_res.data or []
        ordered_qty = {}
        for li in po_items:
            mid = li.get('material_id', 'unknown')
            ordered_qty[mid] = ordered_qty.get(mid, 0) + float(li.get('qty', 0))
        # Build audit rows
        audit_rows = []
        for mat in materials:
            mid = mat['id']
            bal = balances.get(mid, {})
            received = float(bal.get('total_in', 0))
            total_out = float(bal.get('total_out', 0))
            total_used = float(bal.get('total_used', 0))
            total_wasted = float(bal.get('total_wasted', 0))
            expected_remaining = received - total_out
            actual_balance = float(bal.get('balance', 0))
            tolerance = float(mat.get('min_threshold', 0)) * 0.1  # 10% of min_threshold
            discrepancy = abs(expected_remaining - actual_balance) > max(tolerance, 0.01)
            short_delivery = ordered_qty.get(mid, 0) - received
            audit_rows.append({
                'material_id': mid,
                'material_name': mat.get('name', mid),
                'unit': mat.get('unit', ''),
                'ordered_qty': round(ordered_qty.get(mid, 0), 2),
                'received_qty': round(received, 2),
                'consumed_qty': round(total_used, 2),
                'wasted_qty': round(total_wasted, 2),
                'total_out_qty': round(total_out, 2),
                'expected_remaining': round(expected_remaining, 2),
                'actual_balance': round(actual_balance, 2),
                'short_delivery': round(short_delivery, 2),
                'discrepancy_flag': discrepancy,
                'linked_work_item': mat.get('linked_work_item')
            })
        return jsonify(audit_rows)
    except Exception as e:
        logger.error(f'Error in inventory audit: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Expenditure API
# ========================

@app.route('/api/expenditures')
@requires_role_or_override('supervisor')
def api_expenditures():
    if not supabase:
        return jsonify([])
    venture_id = request.args.get('venture_id')
    try:
        if venture_id:
            res = supabase.table('expenditures').select('*').eq('venture_id', venture_id).order('created_at', desc=True).execute()
        else:
            res = supabase.table('expenditures').select('*').order('created_at', desc=True).execute()
        rows = []
        for r in res.data or []:
            data = r.get('data') or {}
            data['id'] = r['id']
            data['created_by'] = r.get('created_by')
            data['created_at'] = r.get('created_at')
            rows.append(data)
        return jsonify(rows)
    except Exception as e:
        logger.error(f'Error fetching expenditures: {e}')
        return jsonify([])


@app.route('/api/expenditure', methods=['POST'])
@requires_role_or_override('supervisor')
def api_expenditure_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    body = request.get_json() or {}
    required = ['venture_id', 'paid_to', 'amount', 'reason', 'date']
    for field in required:
        if field not in body or body[field] in (None, ''):
            return jsonify({'error': f'{field} is required'}), 400
    try:
        entry = {
            'venture_id': body['venture_id'],
            'paid_to': body['paid_to'],
            'amount': float(body['amount']),
            'reason': body['reason'],
            'approved_by': body.get('approved_by', ''),
            'date': body['date']
        }
        user = session.get('user')
        created_by = user.get('email') if isinstance(user, dict) else user
        res = supabase.table('expenditures').insert({
            'venture_id': entry['venture_id'],
            'data': entry,
            'created_by': created_by
        }).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error creating expenditure: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/expenditure/<exp_id>', methods=['DELETE'])
@requires_role_or_override('supervisor')
def api_expenditure_delete(exp_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('expenditures').delete().eq('id', exp_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting expenditure: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Contractor Payments API
# ========================

def _contractor_org_filter():
    """Return org_id for the current session user."""
    user = session.get('user') or {}
    return user.get('org_id')


def _get_org_contract_or_403(contract_id):
    """Fetch a contract row, scoped to the current user's org.
    Returns the contract dict or None if not found / wrong org."""
    org_id = _contractor_org_filter()
    if not org_id:
        return None
    try:
        res = supabase.table('contractor_contracts').select('*').eq('id', contract_id).eq('org_id', org_id).execute()
        if not res.data:
            return None
        return res.data[0]
    except Exception:
        return None


def _get_org_payment_or_403(payment_id):
    """Fetch a payment row, scoped to the current user's org via the contract.
    Returns the payment dict or None if not found / wrong org."""
    org_id = _contractor_org_filter()
    if not org_id:
        return None
    try:
        res = supabase.table('contractor_payments').select('*, contract:contractor_contracts(org_id)').eq('id', payment_id).execute()
        if not res.data:
            return None
        p = res.data[0]
        contract = p.get('contract') or {}
        if isinstance(contract, list):
            contract = contract[0] if contract else {}
        if contract.get('org_id') != org_id:
            return None
        return p
    except Exception:
        return None


def _map_pg_check_error(e):
    """Map a Postgres CHECK-violation to a plain user-facing message."""
    msg = str(e)
    if 'completed_units' in msg and 'total_units' in msg:
        return 'Completed units cannot exceed total units.'
    if 'amount' in msg and '0' in msg:
        return 'Payment amount must be greater than 0.'
    if 'total_amount' in msg and '0' in msg:
        return 'Total amount must be greater than 0.'
    if 'total_units' in msg and '0' in msg:
        return 'Total units must be greater than 0.'
    if 'method' in msg:
        return 'Invalid payment method.'
    if 'status' in msg:
        return 'Invalid contract status.'
    return None


@app.route('/api/contractor-contracts/for-dropdown')
@requires_role_or_override('supervisor')
def api_contractor_contracts_for_dropdown():
    """Lightweight list of active+completed contracts for Day Book dropdown.
    Accessible by admin, manager, and supervisor."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    org_id = _contractor_org_filter()
    if not org_id:
        return jsonify({'error': 'No org_id in session'}), 403
    try:
        res = supabase.table('contractor_contracts').select(
            'id,person_name,work_description,status,venture_id,total_amount'
        ).eq('org_id', org_id).neq('status', 'cancelled').order('created_at', desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching contractor contracts for dropdown: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/contractor-contracts')
@requires_role_or_override('supervisor')
def api_contractor_contracts_list():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    org_id = _contractor_org_filter()
    if not org_id:
        return jsonify({'error': 'No org_id in session'}), 403
    try:
        res = supabase.table('contractor_contracts').select('*').eq('org_id', org_id).order('created_at', desc=True).execute()
        contracts = res.data or []
        # Fetch all non-deleted payments for these contracts in one query
        contract_ids = [c['id'] for c in contracts]
        payments_by_contract = {}
        if contract_ids:
            pay_res = supabase.table('contractor_payments').select('contract_id,amount').eq('is_deleted', False).in_('contract_id', contract_ids).execute()
            for p in (pay_res.data or []):
                cid = p['contract_id']
                payments_by_contract.setdefault(cid, []).append(float(p['amount']))
        result = []
        for c in contracts:
            total_amount = float(c['total_amount'])
            total_units = c['total_units']
            completed_units = c['completed_units']
            pays = payments_by_contract.get(c['id'], [])
            total_paid = sum(pays)
            outstanding = round(total_amount - total_paid, 2)
            per_unit_rate = round(total_amount / total_units, 2) if total_units else 0
            remaining_units = total_units - completed_units
            work_pct = round((completed_units / total_units) * 100, 1) if total_units else 0
            pay_pct = round((total_paid / total_amount) * 100, 1) if total_amount else 0
            risk_delta = round(pay_pct - work_pct, 1)
            c['total_paid'] = total_paid
            c['outstanding_amount'] = outstanding
            c['per_unit_rate'] = per_unit_rate
            c['remaining_units'] = remaining_units
            c['work_progress'] = work_pct
            c['payment_progress'] = pay_pct
            c['risk_delta'] = risk_delta
            c['overpaid_amount'] = abs(outstanding) if outstanding < 0 else 0
            result.append(c)
        return jsonify(result)
    except Exception as e:
        logger.error(f'Error fetching contractor contracts: {e}')
        if 'relation' in str(e) and 'does not exist' in str(e):
            return jsonify({'error': 'Contractor payments tables not found. Please run migration 016_contractor_payments.sql in Supabase.'}), 500
        return jsonify({'error': str(e)}), 500


@app.route('/api/contractor-contracts', methods=['POST'])
@requires_role('admin')
def api_contractor_contracts_create():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    required = ['person_name', 'work_description', 'total_amount', 'total_units']
    for field in required:
        if not body.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    org_id = _contractor_org_filter()
    if not org_id:
        return jsonify({'error': 'No org_id in session'}), 403
    venture_id = body.get('venture_id')
    if venture_id:
        allowed = _allowed_ventures(session['user'])
        if venture_id not in allowed:
            return jsonify({'error': 'Forbidden: venture not in your org'}), 403
    try:
        total_amount = float(body['total_amount'])
        total_units = int(body['total_units'])
        completed_units = int(body.get('completed_units', 0))
        if total_amount <= 0:
            return jsonify({'error': 'Total amount must be greater than 0.'}), 400
        if total_units <= 0:
            return jsonify({'error': 'Total units must be greater than 0.'}), 400
        if completed_units < 0:
            return jsonify({'error': 'Completed units cannot be negative.'}), 400
        if completed_units > total_units:
            return jsonify({'error': 'Completed units cannot exceed total units.'}), 400
        user = session.get('user')
        created_by = user.get('email') if isinstance(user, dict) else str(user)
        status = body.get('status', 'active')
        if status not in ('active', 'completed', 'cancelled'):
            return jsonify({'error': 'Invalid contract status.'}), 400
        res = supabase.table('contractor_contracts').insert({
            'org_id': org_id,
            'venture_id': venture_id or None,
            'person_name': body['person_name'].strip(),
            'work_description': body['work_description'].strip(),
            'total_amount': total_amount,
            'total_units': total_units,
            'completed_units': completed_units,
            'unit_label': body.get('unit_label', 'units').strip() or 'units',
            'status': status,
            'notes': body.get('notes', ''),
            'created_by': created_by,
        }).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error creating contractor contract: {e}')
        friendly = _map_pg_check_error(e)
        if friendly:
            return jsonify({'error': friendly}), 400
        return jsonify({'error': str(e)}), 500


@app.route('/api/contractor-contracts/<contract_id>', methods=['PUT'])
@requires_role('admin')
def api_contractor_contracts_update(contract_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    contract = _get_org_contract_or_403(contract_id)
    if not contract:
        return jsonify({'error': 'Forbidden'}), 403
    body = request.get_json() or {}
    allowed_fields = {}
    for k in ('completed_units', 'status', 'notes', 'unit_label', 'person_name', 'work_description', 'venture_id', 'total_amount', 'total_units'):
        if k in body:
            allowed_fields[k] = body[k]
    if not allowed_fields:
        return jsonify({'error': 'Nothing to update'}), 400
    if 'venture_id' in allowed_fields and allowed_fields['venture_id']:
        allowed_vents = _allowed_ventures(session['user'])
        if allowed_fields['venture_id'] not in allowed_vents:
            return jsonify({'error': 'Forbidden: venture not in your org'}), 403
    if 'completed_units' in allowed_fields:
        total_units = int(allowed_fields.get('total_units', contract['total_units']))
        cu = int(allowed_fields['completed_units'])
        if cu < 0:
            return jsonify({'error': 'Completed units cannot be negative.'}), 400
        if cu > total_units:
            return jsonify({'error': 'Completed units cannot exceed total units.'}), 400
    if 'total_amount' in allowed_fields:
        try:
            ta = float(allowed_fields['total_amount'])
            if ta <= 0:
                return jsonify({'error': 'Total amount must be greater than 0.'}), 400
            allowed_fields['total_amount'] = ta
        except (ValueError, TypeError):
            return jsonify({'error': 'Total amount must be a number.'}), 400
    if 'total_units' in allowed_fields:
        try:
            tu = int(allowed_fields['total_units'])
            if tu <= 0:
                return jsonify({'error': 'Total units must be greater than 0.'}), 400
            allowed_fields['total_units'] = tu
            cu = int(allowed_fields.get('completed_units', contract.get('completed_units', 0)))
            if cu > tu:
                return jsonify({'error': 'Completed units cannot exceed total units.'}), 400
        except (ValueError, TypeError):
            return jsonify({'error': 'Total units must be an integer.'}), 400
    if 'status' in allowed_fields:
        if allowed_fields['status'] not in ('active', 'completed', 'cancelled'):
            return jsonify({'error': 'Invalid contract status.'}), 400
    try:
        user = session.get('user')
        allowed_fields['updated_by'] = user.get('email') if isinstance(user, dict) else str(user)
        supabase.table('contractor_contracts').update(allowed_fields).eq('id', contract_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error updating contractor contract: {e}')
        friendly = _map_pg_check_error(e)
        if friendly:
            return jsonify({'error': friendly}), 400
        return jsonify({'error': str(e)}), 500


@app.route('/api/contractor-contracts/<contract_id>/cancel', methods=['POST'])
@requires_role('admin')
def api_contractor_contracts_cancel(contract_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    contract = _get_org_contract_or_403(contract_id)
    if not contract:
        return jsonify({'error': 'Forbidden'}), 403
    try:
        user = session.get('user')
        supabase.table('contractor_contracts').update({
            'status': 'cancelled',
            'updated_by': user.get('email') if isinstance(user, dict) else str(user),
        }).eq('id', contract_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error cancelling contractor contract: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/contractor-contracts/<contract_id>/payments')
@requires_role_or_override('supervisor')
def api_contractor_payments_list(contract_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    contract = _get_org_contract_or_403(contract_id)
    if not contract:
        return jsonify({'error': 'Forbidden'}), 403
    try:
        res = supabase.table('contractor_payments').select('*').eq('contract_id', contract_id).eq('is_deleted', False).order('payment_date', desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching contractor payments: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/contractor-contracts/<contract_id>/payments', methods=['POST'])
@requires_role_or_override('supervisor')
def api_contractor_payments_create(contract_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    contract = _get_org_contract_or_403(contract_id)
    if not contract:
        return jsonify({'error': 'Forbidden'}), 403
    if contract.get('status') == 'cancelled':
        return jsonify({'error': 'Cannot record payments on a cancelled contract.'}), 400
    body = request.get_json() or {}
    if not body.get('amount') or float(body['amount']) <= 0:
        return jsonify({'error': 'Payment amount must be greater than 0.'}), 400
    if not body.get('payment_date'):
        return jsonify({'error': 'payment_date is required'}), 400
    valid_methods = ('cash', 'upi', 'cheque', 'bank_transfer')
    method = body.get('method', 'cash')
    if method not in valid_methods:
        return jsonify({'error': 'Invalid payment method. Must be one of: ' + ', '.join(valid_methods)}), 400
    try:
        user = session.get('user')
        recorded_by = user.get('email') if isinstance(user, dict) else str(user)
        res = supabase.table('contractor_payments').insert({
            'contract_id': contract_id,
            'amount': float(body['amount']),
            'payment_date': body['payment_date'],
            'method': method,
            'reference': body.get('reference', ''),
            'notes': body.get('notes', ''),
            'recorded_by': recorded_by,
        }).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error creating contractor payment: {e}')
        friendly = _map_pg_check_error(e)
        if friendly:
            return jsonify({'error': friendly}), 400
        return jsonify({'error': str(e)}), 500


@app.route('/api/contractor-payments/<payment_id>/delete', methods=['POST'])
@requires_role('admin')
def api_contractor_payments_soft_delete(payment_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        payment = _get_org_payment_or_403(payment_id)
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
        body = request.get_json() or {}
        if not body.get('deletion_reason') or not body['deletion_reason'].strip():
            return jsonify({'error': 'deletion_reason is required to delete a payment'}), 400
        user = session.get('user')
        deleted_by = user.get('email') if isinstance(user, dict) else str(user)
        if payment.get('is_deleted'):
            return jsonify({'error': 'Payment is already deleted.'}), 400
        supabase.table('contractor_payments').update({
            'is_deleted': True,
            'deletion_reason': body['deletion_reason'].strip(),
            'deleted_by': deleted_by,
            'deleted_at': now_ist().isoformat(),
        }).eq('id', payment_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error soft-deleting contractor payment: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Material Leakage Check API
# ========================

@app.route('/api/materials/leakage-check')
@requires_role_or_override('supervisor')
def api_materials_leakage_check():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    venture_id = request.args.get('venture_id')
    if not venture_id:
        return jsonify({'error': 'venture_id is required'}), 400
    try:
        # Materials
        mats_res = supabase.table('materials').select('*').or_(f'venture_id.eq.{venture_id},venture_id.is.null').execute()
        materials = mats_res.data or []
        # Stock balances
        bal_res = supabase.table('stock_balance').select('*').eq('venture_id', venture_id).execute()
        balances = {b['material_id']: b for b in (bal_res.data or [])}
        # PO line items for ordered qty
        po_li_res = supabase.table('po_line_items').select('*').eq('venture_id', venture_id).execute()
        po_items = po_li_res.data or []
        ordered_qty = {}
        for li in po_items:
            mid = li.get('material_id', 'unknown')
            ordered_qty[mid] = ordered_qty.get(mid, 0) + float(li.get('qty', 0))
        # Stock ledger for received and consumed
        stock_res = supabase.table('stock_ledger').select('*').eq('venture_id', venture_id).execute()
        received_qty = {}
        consumed_qty = {}
        wasted_qty = {}
        for entry in (stock_res.data or []):
            mid = entry.get('material_id', 'unknown')
            if entry.get('entry_type') == 'IN':
                received_qty[mid] = received_qty.get(mid, 0) + float(entry.get('qty', 0))
            elif entry.get('entry_type') == 'OUT':
                if entry.get('is_wastage'):
                    wasted_qty[mid] = wasted_qty.get(mid, 0) + float(entry.get('qty', 0))
                else:
                    consumed_qty[mid] = consumed_qty.get(mid, 0) + float(entry.get('qty', 0))
        # Build result
        rows = []
        for mat in materials:
            mid = mat['id']
            bal = balances.get(mid, {})
            received = received_qty.get(mid, 0)
            consumed = consumed_qty.get(mid, 0)
            wasted = wasted_qty.get(mid, 0)
            total_out = consumed + wasted
            ordered = ordered_qty.get(mid, 0)
            expected_remaining = received - total_out
            actual_balance = float(bal.get('balance', 0))
            tolerance = float(mat.get('min_threshold', 0)) * 0.1
            discrepancy = abs(expected_remaining - actual_balance) > max(tolerance, 0.01)
            short_delivery = ordered - received
            rows.append({
                'material_id': mid,
                'material_name': mat.get('name', mid),
                'unit': mat.get('unit', ''),
                'ordered_qty': round(ordered, 2),
                'received_qty': round(received, 2),
                'consumed_qty': round(consumed, 2),
                'wasted_qty': round(wasted, 2),
                'total_out_qty': round(total_out, 2),
                'expected_remaining': round(expected_remaining, 2),
                'actual_balance': round(actual_balance, 2),
                'short_delivery': round(short_delivery, 2),
                'discrepancy_flag': discrepancy,
                'short_delivery_flag': short_delivery > max(tolerance, 0.01),
                'linked_work_item': mat.get('linked_work_item')
            })
        return jsonify(rows)
    except Exception as e:
        logger.error(f'Error in material leakage check: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Budgets API (Admin-only)
# ========================

@app.route('/api/budgets', methods=['GET', 'POST'])
@requires_role('admin')
def api_budgets():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    if request.method == 'GET':
        venture_id = request.args.get('venture_id')
        q = supabase.table('budgets').select('*')
        if venture_id:
            q = q.eq('venture_id', venture_id)
        res = q.execute()
        return jsonify(res.data or [])
    else:
        body = request.get_json() or {}
        user = session.get('user', {})
        try:
            row = {
                'venture_id': body.get('venture_id'),
                'budget_date': body.get('budget_date'),
                'daily_budget': body.get('daily_budget', 0),
                'interval': body.get('interval', 'daily'),
                'created_by': user.get('id') if isinstance(user, dict) else None,
            }
            res = supabase.table('budgets').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error saving budget: {e}')
            return jsonify({'error': str(e)}), 500


# ========================
# Test DB
# ========================

@app.route('/api/test-db')
@login_required
def api_test_db():
    if not supabase:
        return jsonify({'status': 'error', 'error': 'Supabase not configured'}), 500
    try:
        res = supabase.table('cell_data').select('*', count='exact').execute()
        return jsonify({'status': 'connected', 'rows': res.count if hasattr(res, 'count') and res.count is not None else len(res.data)})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


# ========================
# Interior Design Studio API (Admin/Manager)
# ========================

@app.route('/api/interior-design/generate', methods=['POST'])
@requires_role('manager', 'admin')
def api_interior_design_generate():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500

    room_type = request.form.get('room_type', '').strip()
    style = request.form.get('style', '').strip()
    budget_tier = request.form.get('budget_tier', '').strip()
    area_sqft = request.form.get('area_sqft', '120').strip()
    if not room_type or not style or not budget_tier:
        return jsonify({'error': 'room_type, style, and budget_tier are required'}), 400
    try:
        area_sqft_val = float(area_sqft) if area_sqft else 120
        if area_sqft_val <= 0:
            area_sqft_val = 120
    except ValueError:
        area_sqft_val = 120

    file = request.files.get('image')
    if not file:
        return jsonify({'error': 'image is required'}), 400
    file_bytes = file.read()
    if not file_bytes:
        return jsonify({'error': 'image is empty'}), 400

    ext = (file.filename or '').rsplit('.', 1)[-1].lower() if '.' in (file.filename or '') else 'jpg'
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        ext = 'jpg'
    content_type = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"

    user = session.get('user', {})
    user_id = user.get('id') if isinstance(user, dict) else None
    ts = now_ist().strftime('%Y%m%d_%H%M%S')
    import uuid as _uuid
    path = f"{user_id or 'anon'}_{ts}_{_uuid.uuid4().hex[:8]}.{ext}"

    upload_url, upload_error = upload_bytes_to_storage('interior-uploads', path, file_bytes, content_type)
    if not upload_url:
        return jsonify({'error': 'Failed to upload image to storage', 'details': upload_error}), 500

    # Pollinations must fetch the source image via URL. Prefer the public URL,
    # but fall back to a signed URL if the bucket is private.
    fetchable_url = get_fetchable_image_url('interior-uploads', path)
    if not fetchable_url:
        return jsonify({'error': 'Uploaded image is not reachable; check Supabase Storage bucket permissions'}), 500
    upload_url = fetchable_url

    try:
        res = supabase.table('interior_designs').insert({
            'created_by': user_id,
            'room_type': room_type,
            'style': style,
            'budget_tier': budget_tier,
            'upload_image_url': upload_url,
            'status': 'pending',
            'generated_images': [],
            'cost_estimate': None,
        }).execute()
        if not res.data:
            return jsonify({'error': 'Failed to create design record'}), 500
        design_id = res.data[0]['id']
    except Exception as e:
        logger.error(f'Error creating interior design record: {e}')
        return jsonify({'error': 'Failed to create design record'}), 500

    def generate_in_background(did, img_url, rt, st, bt, sqft):
        from time import sleep
        try:
            prompt = enhance_design_prompt(rt, st, bt, sqft)
            supabase.table('interior_designs').update({'enhanced_prompt': prompt}).eq('id', did).execute()
            generated = []
            # Single variant keeps generation reliably under 30 seconds on the free tier.
            for idx, seed in enumerate((0,)):
                if idx > 0:
                    sleep(4)
                ok, result = generate_room_design(img_url, prompt, seed)
                if ok:
                    out_path = f"generated_{did}_{seed}.jpg"
                    out_url, out_err = upload_bytes_to_storage('interior-uploads', out_path, result, 'image/jpeg')
                    if not out_url:
                        # Fallback: embed generated image as base64 data URL.
                        out_url = f"data:image/jpeg;base64,{base64.b64encode(result).decode('ascii')}"
                    generated.append({'seed': seed, 'url': out_url})
                else:
                    generated.append({'seed': seed, 'url': None, 'error': result})
                    app.logger.warning(f'Design {did} seed {seed} failed: {result}')
            cost = compute_design_cost_estimate(rt, bt, sqft)
            successful = [g for g in generated if g.get('url')]
            failed = [g for g in generated if not g.get('url')]
            status = 'completed' if successful else 'failed'
            error_message = None
            if failed:
                parts = [g.get('error') or 'unknown error' for g in failed]
                if not successful:
                    error_message = '; '.join(parts)[:500]
                else:
                    error_message = 'Some images failed to generate'
            supabase.table('interior_designs').update({
                'generated_images': generated,
                'cost_estimate': cost,
                'status': status,
                'error_message': error_message
            }).eq('id', did).execute()
        except Exception as e:
            logger.info(f'Background generation error for {did}: {e}')
            try:
                supabase.table('interior_designs').update({
                    'status': 'failed',
                    'error_message': str(e)
                }).eq('id', did).execute()
            except Exception:
                pass

    import threading
    threading.Thread(target=generate_in_background, args=(
        design_id, upload_url, room_type, style, budget_tier, area_sqft_val
    ), daemon=True).start()

    return jsonify({'id': design_id, 'status': 'pending'}), 200


@app.route('/api/interior-design/<design_id>/status')
@requires_role_or_override('supervisor')
def api_interior_design_status(design_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        res = supabase.table('interior_designs').select('*').eq('id', design_id).limit(1).execute()
        if not res.data:
            return jsonify({'error': 'Design not found'}), 404
        row = res.data[0]
        return jsonify({
            'id': row['id'],
            'status': row['status'],
            'room_type': row['room_type'],
            'style': row['style'],
            'budget_tier': row['budget_tier'],
            'upload_image_url': row['upload_image_url'],
            'enhanced_prompt': row.get('enhanced_prompt'),
            'generated_images': row.get('generated_images') or [],
            'cost_estimate': row.get('cost_estimate'),
            'error_message': row.get('error_message'),
            'created_at': row.get('created_at')
        })
    except Exception as e:
        logger.error(f'Error fetching design status: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/interior-design/history')
@login_required
def api_interior_design_history():
    if not supabase:
        return jsonify([])
    try:
        res = supabase.table('interior_designs').select('*').order('created_at', desc=True).limit(100).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching design history: {e}')
        return jsonify([])


@app.route('/api/interior-design/<design_id>', methods=['DELETE'])
@requires_role('manager', 'admin')
def api_interior_design_delete(design_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('interior_designs').delete().eq('id', design_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting interior design: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Construction Marketplace API
# ========================

@app.route('/api/marketplace/materials')
@requires_role_or_override('supervisor')
def api_marketplace_materials():
    if not supabase:
        return jsonify([])
    category = request.args.get('category', '').strip()
    q = supabase.table('marketplace_materials').select('*').eq('is_active', True)
    if category:
        q = q.eq('category', category)
    try:
        res = q.order('category', desc=False).order('name', desc=False).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching marketplace materials: {e}')
        return jsonify([])


@app.route('/api/marketplace/materials/<material_id>/suppliers')
@requires_role_or_override('supervisor')
def api_marketplace_suppliers(material_id):
    if not supabase:
        return jsonify([])
    min_price = request.args.get('min_price')
    max_price = request.args.get('max_price')
    verified_only = request.args.get('verified_only', '').lower() == 'true'
    try:
        q = supabase.table('marketplace_suppliers').select('*').eq('material_id', material_id)
        if verified_only:
            q = q.ilike('trust_level', '%Verified%')
        res = q.execute()
        rows = res.data or []
        if min_price is not None:
            try:
                min_p = float(min_price)
                rows = [r for r in rows if float(r.get('price_low', 0)) >= min_p]
            except ValueError:
                pass
        if max_price is not None:
            try:
                max_p = float(max_price)
                rows = [r for r in rows if float(r.get('price_low', 0)) <= max_p]
            except ValueError:
                pass
        rows.sort(key=lambda r: (
            0 if 'verified' in (r.get('trust_level') or '').lower() else 1,
            float(r.get('price_low', 0))
        ))
        return jsonify(rows[:5])
    except Exception as e:
        logger.error(f'Error fetching marketplace suppliers: {e}')
        return jsonify([])


@app.route('/api/marketplace/materials', methods=['POST'])
@requires_role('admin')
def api_marketplace_material_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    body = request.get_json() or {}
    required = ['category', 'name', 'unit']
    for field in required:
        if not body.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    try:
        row = {
            'category': body['category'],
            'name': body['name'],
            'unit': body['unit'],
            'description': body.get('description', ''),
            'is_active': body.get('is_active', True),
        }
        if body.get('id'):
            row['id'] = body['id']
            supabase.table('marketplace_materials').upsert(row, on_conflict='id').execute()
            return jsonify({'success': True})
        res = supabase.table('marketplace_materials').insert(row).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error saving marketplace material: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/marketplace/suppliers', methods=['POST'])
@requires_role('admin')
def api_marketplace_supplier_post():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    body = request.get_json() or {}
    required = ['material_id', 'company_name', 'brand_name', 'price_low', 'price_high']
    for field in required:
        if field not in body or body[field] in (None, ''):
            return jsonify({'error': f'{field} is required'}), 400
    try:
        row = {
            'material_id': body['material_id'],
            'company_name': body['company_name'],
            'brand_name': body['brand_name'],
            'price_low': float(body['price_low']),
            'price_high': float(body['price_high']),
            'currency': body.get('currency', 'INR'),
            'trust_level': body.get('trust_level', ''),
            'email': body.get('email', ''),
            'phone': body.get('phone', ''),
            'price_last_verified_at': body.get('price_last_verified_at'),
            'source_note': body.get('source_note', ''),
        }
        if body.get('id'):
            row['id'] = body['id']
            supabase.table('marketplace_suppliers').upsert(row, on_conflict='id').execute()
            return jsonify({'success': True})
        res = supabase.table('marketplace_suppliers').insert(row).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error saving marketplace supplier: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/marketplace/materials/<material_id>', methods=['DELETE'])
@requires_role('admin')
def api_marketplace_material_delete(material_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('marketplace_suppliers').delete().eq('material_id', material_id).execute()
        supabase.table('marketplace_materials').delete().eq('id', material_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting marketplace material: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/marketplace/suppliers/<supplier_id>', methods=['DELETE'])
@requires_role('admin')
def api_marketplace_supplier_delete(supplier_id):
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('marketplace_suppliers').delete().eq('id', supplier_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deleting marketplace supplier: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/marketplace/seed', methods=['POST'])
@requires_role('admin')
def api_marketplace_seed():
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        run_marketplace_seed()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error seeding marketplace: {e}')
        return jsonify({'error': str(e)}), 500


# ============================================================
# RWA MODULE: Foundation (Phase 1)
# ============================================================

def sync_completed_flats_to_flats_table(venture_id=None, block=None, floor=None):
    """Read cell_data for a given block/floor, check if all work items are green,
    and upsert/update the corresponding row in flats to construction_status='completed'.
    Does NOT touch any cell_data — reads only."""
    if not supabase:
        return {'error': 'Supabase not connected'}
    try:
        query = supabase.table('cell_data').select('*')
        if venture_id:
            query = query.filter('data->>venture_id', 'eq', venture_id)
        if block:
            query = query.filter('data->>block', 'eq', block)
        if floor:
            query = query.filter('data->>floor', 'eq', str(floor))
        res = query.execute()
        rows = res.data or []

        # Group cells by block|floor|flat_number
        flat_map = {}
        for row in rows:
            d = row.get('data') or {}
            b = d.get('block', '')
            f = d.get('floor', '')
            flat_num = d.get('flat', '')
            if not b or not f or not flat_num:
                continue
            key = (b, f, flat_num)
            if key not in flat_map:
                flat_map[key] = {'cells': [], 'all_green': True}
            color = d.get('color', '')
            flat_map[key]['cells'].append({'id': row['id'], 'color': color})
            if color != 'green':
                flat_map[key]['all_green'] = False

        updated = []
        for (b, f, flat_num), info in flat_map.items():
            if not info['cells']:
                continue
            status = 'completed' if info['all_green'] else 'pending'
            existing = supabase.table('flats').select('id, construction_status').eq(
                'block', b).eq('floor', f).eq('flat_number', flat_num).execute()
            if existing.data:
                flat_row = existing.data[0]
                if flat_row['construction_status'] != status:
                    supabase.table('flats').update({
                        'construction_status': status
                    }).eq('id', flat_row['id']).execute()
                    updated.append({'block': b, 'floor': f, 'flat': flat_num, 'status': status})
            else:
                supabase.table('flats').insert({
                    'block': b, 'floor': f, 'flat_number': flat_num,
                    'construction_status': status
                }).execute()
                updated.append({'block': b, 'floor': f, 'flat': flat_num, 'status': status, 'created': True})

        return {'synced': len(updated), 'flats': updated}
    except Exception as e:
        logger.error(f'Error syncing completed flats: {e}')
        return {'error': str(e)}


@app.route('/api/rwa/sync-completed-flats', methods=['POST'])
@requires_role('admin', 'manager')
def api_rwa_sync_completed_flats():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    result = sync_completed_flats_to_flats_table(
        venture_id=body.get('venture_id'),
        block=body.get('block'),
        floor=body.get('floor')
    )
    if 'error' in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route('/api/rwa/flats')
@requires_role('admin', 'manager')
def api_rwa_flats():
    if not supabase:
        return jsonify([]), 500
    try:
        res = supabase.table('flats').select('*').order('block', desc=False).order('floor', desc=False).order('flat_number', desc=False).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching flats: {e}')
        return jsonify([]), 500


@app.route('/api/rwa/emergency-contacts')
@visitor_login_required
def api_rwa_emergency_contacts():
    if not supabase:
        return jsonify([]), 500
    try:
        res = supabase.table('emergency_contacts').select('*').eq('active', True).order('label', desc=False).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching emergency contacts: {e}')
        return jsonify([]), 500


@app.route('/rwa-admin')
@login_required
def rwa_admin_page():
    return render_template('rwa_admin.html')


# ============================================================
# RWA MODULE: Standard Tier (Phase 2)
# ============================================================

def _get_rwa_session_user():
    """Return (user_dict, role_string) for the current session — works for
    resident, security, and admin/manager sessions."""
    resident = session.get('visitor_user')
    if resident:
        return resident, 'resident'
    security = session.get('security_user')
    if security:
        return security, 'security'
    user = session.get('user')
    if user:
        role = user.get('role', 'admin') if isinstance(user, dict) else 'admin'
        return user, role
    return None, None


# --- Deliveries ---

@app.route('/api/rwa/delivery', methods=['POST'])
@visitor_login_required
def api_rwa_delivery_create():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    if not body.get('resident_id'):
        return jsonify({'error': 'resident_id is required'}), 400
    try:
        user, role = _get_rwa_session_user()
        security_id = user.get('id') if role == 'security' else None
        import uuid as _uuid
        row = {
            'resident_id': body['resident_id'],
            'security_id': security_id,
            'courier_name': body.get('courier_name', ''),
            'delivery_person_name': body.get('delivery_person_name', ''),
            'vehicle_number': body.get('vehicle_number', ''),
            'qr_code': str(_uuid.uuid4()),
            'parcel_photo_url': body.get('parcel_photo_url', ''),
            'status': 'arrived',
        }
        res = supabase.table('deliveries').insert(row).execute()
        data = res.data[0] if res.data else None
        return jsonify({'success': True, 'id': data['id'] if data else None, 'qr_code': data.get('qr_code') if data else None})
    except Exception as e:
        logger.error(f'Error creating delivery: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/delivery/<delivery_id>', methods=['PATCH'])
@visitor_login_required
def api_rwa_delivery_patch(delivery_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    allowed = {}
    if 'status' in body and body['status'] in ('arrived', 'inside', 'collected', 'returned', 'expired'):
        allowed['status'] = body['status']
        if body['status'] == 'collected':
            allowed['collected_at'] = now_ist().isoformat()
            allowed['exit_time'] = now_ist().isoformat()
            allowed['expires_at'] = None
        if body['status'] == 'returned':
            allowed['exit_time'] = now_ist().isoformat()
            allowed['expires_at'] = None
        if body['status'] == 'inside':
            allowed['entry_time'] = now_ist().isoformat()
            allowed['expires_at'] = (now_ist() + timedelta(minutes=20)).isoformat()
    if 'alerted' in body:
        allowed['alerted'] = bool(body['alerted'])
    if not allowed:
        return jsonify({'error': 'Nothing to update'}), 400
    try:
        supabase.table('deliveries').update(allowed).eq('id', delivery_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error updating delivery: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/deliveries')
@visitor_login_required
def api_rwa_deliveries():
    if not supabase:
        return jsonify([]), 500
    try:
        user, role = _get_rwa_session_user()
        q = supabase.table('deliveries').select('*, residents(name, mobile, block, floor, flat)')
        if role == 'resident':
            q = q.eq('resident_id', user['id'])
        res = q.order('arrived_at', desc=True).execute()
        rows = []
        for r in res.data or []:
            rd = r.get('residents') or {}
            rows.append({
                'id': r['id'], 'resident_id': r['resident_id'],
                'resident_name': rd.get('name'), 'resident_mobile': rd.get('mobile'),
                'block': rd.get('block'), 'floor': rd.get('floor'), 'flat': rd.get('flat'),
                'courier_name': r.get('courier_name'), 'delivery_person_name': r.get('delivery_person_name'),
                'vehicle_number': r.get('vehicle_number'), 'qr_code': r.get('qr_code'),
                'status': r.get('status'), 'arrived_at': r.get('arrived_at'),
                'collected_at': r.get('collected_at'), 'entry_time': r.get('entry_time'),
                'exit_time': r.get('exit_time'), 'expires_at': r.get('expires_at'),
                'alerted': r.get('alerted', False)
            })
        return jsonify(rows)
    except Exception as e:
        logger.error(f'Error fetching deliveries: {e}')
        return jsonify([]), 500


@app.route('/api/rwa/delivery/<delivery_id>/qr')
@visitor_login_required
def api_rwa_delivery_qr(delivery_id):
    """Generate a QR code image for a delivery entry pass.
    Scanning the QR at the gate starts the 20-minute collection timer."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        res = supabase.table('deliveries').select(
            '*, residents(name, mobile, block, floor, flat)'
        ).eq('id', delivery_id).execute()
        if not res.data:
            return jsonify({'error': 'Delivery not found'}), 404
        d = res.data[0]
        if d.get('status') in ('collected', 'returned'):
            return jsonify({'error': 'Delivery already completed'}), 400
        if not d.get('qr_code'):
            return jsonify({'error': 'No QR code assigned'}), 400

        import qrcode as _qrcode
        import io as _io
        import json as _json

        rd = d.get('residents') or {}
        qr_payload = _json.dumps({
            'type': 'rwa_delivery_pass',
            'id': d['id'],
            'qr_code': d.get('qr_code'),
            'resident_name': rd.get('name', ''),
            'flat': f"{rd.get('block','')}-{rd.get('floor','')}-{rd.get('flat','')}",
            'delivery_person_name': d.get('delivery_person_name', ''),
            'vehicle_number': d.get('vehicle_number', ''),
            'issued_at': now_ist().isoformat(),
        })

        qr = _qrcode.QRCode(version=1, error_correction=_qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(qr_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = _io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)

        from flask import send_file as _send_file
        return _send_file(buf, mimetype='image/png')
    except Exception as e:
        logger.error(f'Error generating delivery QR: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/delivery/scan', methods=['POST'])
@visitor_login_required
def api_rwa_delivery_scan():
    """Security scans a delivery QR pass at the gate.
    If status is 'arrived', mark 'inside' and start 20-minute timer.
    If status is 'inside', mark exit/collected."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role not in ('security', 'admin', 'manager'):
        return jsonify({'error': 'Security only'}), 403
    body = request.get_json() or {}
    payload = body.get('payload')
    if not payload:
        return jsonify({'error': 'payload is required'}), 400

    import json as _json
    try:
        data = _json.loads(payload) if isinstance(payload, str) else payload
    except Exception:
        return jsonify({'error': 'Invalid QR payload'}), 400

    if data.get('type') != 'rwa_delivery_pass':
        return jsonify({'error': 'Not a delivery pass QR'}), 400

    delivery_id = data.get('id')
    qr_code = data.get('qr_code')
    if not delivery_id or not qr_code:
        return jsonify({'error': 'Missing delivery ID or QR code'}), 400

    try:
        res = supabase.table('deliveries').select(
            '*, residents(name, mobile, block, floor, flat)'
        ).eq('id', delivery_id).execute()
        if not res.data:
            return jsonify({'error': 'Delivery not found'}), 404
        d = res.data[0]
        if d.get('qr_code') != qr_code:
            return jsonify({'error': 'Invalid QR code'}), 400
        rd = d.get('residents') or {}

        if d.get('status') in ('collected', 'returned'):
            return jsonify({'error': 'Delivery already completed', 'status': d.get('status')}), 409

        now = now_ist().isoformat()
        if d.get('status') == 'inside':
            supabase.table('deliveries').update({
                'status': 'collected',
                'exit_time': now,
                'expires_at': None,
                'alerted': False
            }).eq('id', delivery_id).execute()
            return jsonify({
                'success': True,
                'action': 'exit',
                'status': 'collected',
                'delivery_person_name': d.get('delivery_person_name'),
                'resident_name': rd.get('name'),
                'flat': f"{rd.get('block','')}-{rd.get('floor','')}-{rd.get('flat','')}",
            })

        supabase.table('deliveries').update({
            'status': 'inside',
            'entry_time': now,
            'expires_at': (now_ist() + timedelta(minutes=20)).isoformat(),
            'security_id': user.get('id') if role == 'security' else None,
            'alerted': False
        }).eq('id', delivery_id).execute()

        return jsonify({
            'success': True,
            'action': 'entry',
            'status': 'inside',
            'delivery_person_name': d.get('delivery_person_name'),
            'vehicle_number': d.get('vehicle_number'),
            'resident_name': rd.get('name'),
            'resident_mobile': rd.get('mobile'),
            'flat': f"{rd.get('block','')}-{rd.get('floor','')}-{rd.get('flat','')}",
            'expires_at': (now_ist() + timedelta(minutes=20)).isoformat(),
        })
    except Exception as e:
        logger.error(f'Error scanning delivery QR: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/delivery/alerts')
@visitor_login_required
def api_rwa_delivery_alerts():
    """Return active deliveries whose 20-minute timer has expired.
    Security dashboard polls this and can call the resident."""
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if role not in ('security', 'admin', 'manager'):
        return jsonify([]), 403
    try:
        now = now_ist().isoformat()
        res = supabase.table('deliveries').select(
            '*, residents(name, mobile, block, floor, flat)'
        ).eq('status', 'inside').lt('expires_at', now).order('expires_at').execute()
        rows = []
        for r in res.data or []:
            rd = r.get('residents') or {}
            rows.append({
                'id': r['id'], 'resident_name': rd.get('name'), 'resident_mobile': rd.get('mobile'),
                'block': rd.get('block'), 'floor': rd.get('floor'), 'flat': rd.get('flat'),
                'delivery_person_name': r.get('delivery_person_name'), 'vehicle_number': r.get('vehicle_number'),
                'entry_time': r.get('entry_time'), 'expires_at': r.get('expires_at'),
                'alerted': r.get('alerted', False)
            })
        return jsonify(rows)
    except Exception as e:
        logger.error(f'Error fetching delivery alerts: {e}')
        return jsonify([]), 500


@app.route('/api/rwa/delivery/<delivery_id>/exit', methods=['POST'])
@visitor_login_required
def api_rwa_delivery_exit(delivery_id):
    """Manually mark a delivery as exited/collected."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role not in ('security', 'admin', 'manager', 'resident'):
        return jsonify({'error': 'Not allowed'}), 403
    try:
        supabase.table('deliveries').update({
            'status': 'collected',
            'exit_time': now_ist().isoformat(),
            'expires_at': None,
            'alerted': False
        }).eq('id', delivery_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error marking delivery exit: {e}')
        return jsonify({'error': str(e)}), 500


# --- Daily Help ---

@app.route('/api/rwa/daily-help', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_daily_help():
    if not supabase:
        return jsonify([]), 500
    if request.method == 'GET':
        try:
            res = supabase.table('daily_help').select('*').eq('active', True).order('name').execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching daily help: {e}')
            return jsonify([]), 500
    else:
        body = request.get_json() or {}
        if not body.get('name'):
            return jsonify({'error': 'name is required'}), 400
        try:
            row = {
                'name': body['name'],
                'mobile': body.get('mobile', ''),
                'role_type': body.get('role_type', ''),
                'photo_url': body.get('photo_url', ''),
            }
            res = supabase.table('daily_help').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error creating daily help: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/daily-help/<help_id>', methods=['PATCH', 'DELETE'])
@visitor_login_required
def api_rwa_daily_help_patch(help_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        if request.method == 'DELETE':
            supabase.table('daily_help').update({'active': False}).eq('id', help_id).execute()
            return jsonify({'success': True})
        else:
            body = request.get_json() or {}
            allowed = {k: v for k, v in body.items() if k in ('name', 'mobile', 'role_type', 'photo_url', 'active')}
            if not allowed:
                return jsonify({'error': 'Nothing to update'}), 400
            supabase.table('daily_help').update(allowed).eq('id', help_id).execute()
            return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error updating daily help: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/daily-help/<help_id>/attendance', methods=['POST', 'GET'])
@visitor_login_required
def api_rwa_daily_help_attendance(help_id):
    if not supabase:
        return jsonify([]), 500
    if request.method == 'GET':
        try:
            res = supabase.table('daily_help_attendance').select('*').eq(
                'daily_help_id', help_id).order('check_in', desc=True).limit(50).execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching attendance: {e}')
            return jsonify([]), 500
    else:
        body = request.get_json() or {}
        action = body.get('action', 'check_in')
        try:
            user, role = _get_rwa_session_user()
            security_id = user.get('id') if role == 'security' else None
            if action == 'check_in':
                row = {
                    'daily_help_id': help_id,
                    'check_in': now_ist().isoformat(),
                    'verified_by': security_id,
                }
                res = supabase.table('daily_help_attendance').insert(row).execute()
                return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
            elif action == 'check_out':
                att_id = body.get('attendance_id')
                if not att_id:
                    open_att = supabase.table('daily_help_attendance').select('id').eq(
                        'daily_help_id', help_id).is_('check_out', 'null').order('check_in', desc=True).limit(1).execute()
                    if not open_att.data:
                        return jsonify({'error': 'No open check-in found'}), 404
                    att_id = open_att.data[0]['id']
                supabase.table('daily_help_attendance').update({
                    'check_out': now_ist().isoformat()
                }).eq('id', att_id).execute()
                return jsonify({'success': True})
            return jsonify({'error': 'Invalid action'}), 400
        except Exception as e:
            logger.error(f'Error recording attendance: {e}')
            return jsonify({'error': str(e)}), 500


# --- Resident Vehicles ---

@app.route('/api/rwa/vehicles', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_vehicles():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if request.method == 'GET':
        try:
            q = supabase.table('resident_vehicles').select('*, residents(name, block, floor, flat)')
            if role == 'resident':
                q = q.eq('resident_id', user['id'])
            res = q.order('created_at', desc=True).execute()
            rows = []
            for r in res.data or []:
                rd = r.get('residents') or {}
                rows.append({
                    'id': r['id'], 'resident_id': r['resident_id'],
                    'resident_name': rd.get('name'), 'block': rd.get('block'),
                    'floor': rd.get('floor'), 'flat': rd.get('flat'),
                    'vehicle_number': r['vehicle_number'], 'vehicle_type': r.get('vehicle_type')
                })
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching vehicles: {e}')
            return jsonify([]), 500
    else:
        if role != 'resident':
            return jsonify({'error': 'Only residents can add vehicles'}), 403
        body = request.get_json() or {}
        if not body.get('vehicle_number'):
            return jsonify({'error': 'vehicle_number is required'}), 400
        try:
            row = {
                'resident_id': user['id'],
                'vehicle_number': body['vehicle_number'].upper().strip(),
                'vehicle_type': body.get('vehicle_type', ''),
            }
            res = supabase.table('resident_vehicles').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
                return jsonify({'error': 'Vehicle number already registered'}), 409
            logger.error(f'Error adding vehicle: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/vehicle-search')
@visitor_login_required
def api_rwa_vehicle_search():
    if not supabase:
        return jsonify([]), 500
    number = request.args.get('number', '').strip().upper()
    if not number:
        return jsonify([]), 400
    try:
        results = []
        # Search resident_vehicles
        rv_res = supabase.table('resident_vehicles').select(
            '*, residents(name, mobile, block, floor, flat)'
        ).ilike('vehicle_number', f'%{number}%').execute()
        for r in rv_res.data or []:
            rd = r.get('residents') or {}
            results.append({
                'source': 'resident', 'vehicle_number': r['vehicle_number'],
                'vehicle_type': r.get('vehicle_type'),
                'resident_name': rd.get('name'), 'mobile': rd.get('mobile'),
                'block': rd.get('block'), 'floor': rd.get('floor'), 'flat': rd.get('flat')
            })
        # Search visitor_requests
        vr_res = supabase.table('visitor_requests').select(
            '*, residents(name, mobile, block, floor, flat)'
        ).ilike('vehicle_number', f'%{number}%').order('created_at', desc=True).limit(20).execute()
        for r in vr_res.data or []:
            rd = r.get('residents') or {}
            results.append({
                'source': 'visitor', 'vehicle_number': r.get('vehicle_number', ''),
                'visitor_name': r.get('visitor_name'), 'visitor_mobile': r.get('visitor_mobile'),
                'status': r.get('status'), 'purpose': r.get('purpose'),
                'resident_name': rd.get('name'), 'mobile': rd.get('mobile'),
                'block': rd.get('block'), 'floor': rd.get('floor'), 'flat': rd.get('flat'),
                'entry_time': r.get('entry_time'), 'exit_time': r.get('exit_time')
            })
        return jsonify(results)
    except Exception as e:
        logger.error(f'Error searching vehicles: {e}')
        return jsonify([]), 500


# --- Kids Checkout ---

@app.route('/api/rwa/kids-checkout', methods=['POST', 'GET'])
@visitor_login_required
def api_rwa_kids_checkout():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if request.method == 'GET':
        try:
            q = supabase.table('kids_checkout').select('*, residents(name, block, floor, flat)')
            if role == 'resident':
                q = q.eq('resident_id', user['id'])
            res = q.order('created_at', desc=True).limit(50).execute()
            rows = []
            for r in res.data or []:
                rd = r.get('residents') or {}
                rows.append({
                    'id': r['id'], 'resident_id': r['resident_id'],
                    'resident_name': rd.get('name'), 'block': rd.get('block'),
                    'floor': rd.get('floor'), 'flat': rd.get('flat'),
                    'child_name': r['child_name'], 'picked_up_by': r['picked_up_by'],
                    'otp_verified_at': r.get('otp_verified_at'),
                    'created_at': r.get('created_at')
                })
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching kids checkout: {e}')
            return jsonify([]), 500
    else:
        body = request.get_json() or {}
        if not body.get('resident_id') or not body.get('child_name') or not body.get('picked_up_by'):
            return jsonify({'error': 'resident_id, child_name, and picked_up_by are required'}), 400
        try:
            code = generate_otp()
            row = {
                'resident_id': body['resident_id'],
                'child_name': body['child_name'],
                'picked_up_by': body['picked_up_by'],
                'otp_code': code,
            }
            res = supabase.table('kids_checkout').insert(row).execute()
            if not res.data:
                return jsonify({'error': 'Failed to create checkout record'}), 500
            kid_id = res.data[0]['id']
            # Send OTP to resident
            r_res = supabase.table('residents').select('mobile').eq('id', body['resident_id']).execute()
            mobile = r_res.data[0]['mobile'] if r_res.data else ''
            if mobile:
                send_otp(mobile, code)
            return jsonify({'success': True, 'id': kid_id, 'otp': code})
        except Exception as e:
            logger.error(f'Error creating kids checkout: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/kids-checkout/<kid_id>/verify', methods=['POST'])
@visitor_login_required
def api_rwa_kids_checkout_verify(kid_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    code = body.get('otp', '').strip()
    if not code:
        return jsonify({'error': 'otp is required'}), 400
    try:
        res = supabase.table('kids_checkout').select('*').eq('id', kid_id).execute()
        if not res.data:
            return jsonify({'error': 'Record not found'}), 404
        row = res.data[0]
        if row.get('otp_verified_at'):
            return jsonify({'error': 'Already verified'}), 400
        if row.get('otp_code') != code:
            return jsonify({'error': 'Invalid OTP'}), 400
        user, role = _get_rwa_session_user()
        security_id = user.get('id') if role == 'security' else None
        supabase.table('kids_checkout').update({
            'otp_verified_at': now_ist().isoformat(),
            'security_id': security_id
        }).eq('id', kid_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error verifying kids checkout: {e}')
        return jsonify({'error': str(e)}), 500


# --- Directory ---

@app.route('/api/rwa/directory')
@visitor_login_required
def api_rwa_directory():
    if not supabase:
        return jsonify([]), 500
    try:
        res = supabase.table('residents').select(
            'id, name, mobile, block, floor, flat, directory_opt_in'
        ).eq('active', True).order('block').order('floor').order('flat').execute()
        rows = []
        for r in res.data or []:
            row = {
                'id': r['id'], 'name': r['name'],
                'block': r['block'], 'floor': r['floor'], 'flat': r['flat'],
            }
            if r.get('directory_opt_in'):
                row['mobile'] = r['mobile']
            else:
                row['mobile'] = '****' + r['mobile'][-4:] if r.get('mobile') and len(r['mobile']) >= 4 else 'Hidden'
            rows.append(row)
        return jsonify(rows)
    except Exception as e:
        logger.error(f'Error fetching directory: {e}')
        return jsonify([]), 500


@app.route('/api/rwa/directory/opt-in', methods=['POST'])
@visitor_login_required
def api_rwa_directory_opt_in():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role != 'resident':
        return jsonify({'error': 'Only residents can update opt-in'}), 403
    body = request.get_json() or {}
    try:
        supabase.table('residents').update({
            'directory_opt_in': body.get('opt_in', False)
        }).eq('id', user['id']).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error updating directory opt-in: {e}')
        return jsonify({'error': str(e)}), 500


# --- Pre-approved visitor requests ---

@app.route('/api/rwa/pre-approve', methods=['POST'])
@visitor_login_required
def api_rwa_pre_approve():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role != 'resident':
        return jsonify({'error': 'Only residents can pre-approve'}), 403
    body = request.get_json() or {}
    if not body.get('visitor_name'):
        return jsonify({'error': 'visitor_name is required'}), 400
    try:
        row = {
            'resident_id': user['id'],
            'visitor_name': body['visitor_name'],
            'visitor_mobile': body.get('visitor_mobile', ''),
            'purpose': body.get('purpose', ''),
            'visitor_count': int(body.get('visitor_count', 1) or 1),
            'vehicle_number': body.get('vehicle_number', ''),
            'status': 'approved',
            'is_pre_approved': True,
            'otp_code': generate_otp(),
            'entry_time': now_ist().isoformat()
        }
        res = supabase.table('visitor_requests').insert(row).execute()
        visitor_id = res.data[0]['id'] if res.data else None
        return jsonify({'success': True, 'id': visitor_id})
    except Exception as e:
        logger.error(f'Error pre-approving visitor: {e}')
        return jsonify({'error': str(e)}), 500


# --- QR: Visitor Pass Generation & Scanning ---

@app.route('/api/rwa/visitor-pass/<visitor_id>/qr')
@visitor_login_required
def api_rwa_visitor_pass_qr(visitor_id):
    """Generate a QR code image for a pre-approved visitor pass.
    The QR encodes a JSON payload with the visitor_request ID and a verification URL."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        res = supabase.table('visitor_requests').select(
            '*, residents(name, block, floor, flat)'
        ).eq('id', visitor_id).execute()
        if not res.data:
            return jsonify({'error': 'Visitor pass not found'}), 404
        vr = res.data[0]
        if vr.get('status') in ('rejected', 'completed'):
            return jsonify({'error': 'Pass is no longer valid'}), 400

        import qrcode as _qrcode
        import io as _io
        import json as _json

        qr_payload = _json.dumps({
            'type': 'rwa_visitor_pass',
            'id': vr['id'],
            'visitor_name': vr.get('visitor_name', ''),
            'resident_name': (vr.get('residents') or {}).get('name', ''),
            'flat': f"{(vr.get('residents') or {}).get('block','')}-{(vr.get('residents') or {}).get('floor','')}-{(vr.get('residents') or {}).get('flat','')}",
            'status': vr.get('status', ''),
            'issued_at': now_ist().isoformat(),
        })

        qr = _qrcode.QRCode(version=1, error_correction=_qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(qr_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = _io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)

        from flask import send_file as _send_file
        return _send_file(buf, mimetype='image/png')
    except Exception as e:
        logger.error(f'Error generating visitor QR: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/visitor-pass/scan', methods=['POST'])
@visitor_login_required
def api_rwa_visitor_pass_scan():
    """Security scans a visitor QR pass at the gate.
    Accepts the QR payload JSON, verifies the visitor_request exists and is pre-approved,
    and marks entry if status is 'approved' (not yet inside)."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role not in ('security', 'admin', 'manager'):
        return jsonify({'error': 'Security only'}), 403
    body = request.get_json() or {}
    payload = body.get('payload')
    if not payload:
        return jsonify({'error': 'payload is required'}), 400

    import json as _json
    try:
        data = _json.loads(payload) if isinstance(payload, str) else payload
    except Exception:
        return jsonify({'error': 'Invalid QR payload'}), 400

    if data.get('type') != 'rwa_visitor_pass':
        return jsonify({'error': 'Not a visitor pass QR'}), 400

    visitor_id = data.get('id')
    if not visitor_id:
        return jsonify({'error': 'No pass ID in QR'}), 400

    try:
        res = supabase.table('visitor_requests').select(
            '*, residents(name, block, floor, flat)'
        ).eq('id', visitor_id).execute()
        if not res.data:
            return jsonify({'error': 'Pass not found'}), 404
        vr = res.data[0]
        rd = vr.get('residents') or {}

        if vr.get('status') in ('rejected', 'completed'):
            return jsonify({'error': 'Pass is no longer valid'}), 409

        if vr.get('status') == 'inside':
            return jsonify({'error': 'Visitor already inside', 'visitor_name': vr.get('visitor_name')}), 409

        # Mark entry
        supabase.table('visitor_requests').update({
            'status': 'inside',
            'entry_time': now_ist().isoformat(),
            'security_id': user.get('id') if role == 'security' else None,
        }).eq('id', visitor_id).execute()

        return jsonify({
            'success': True,
            'visitor_name': vr.get('visitor_name'),
            'visitor_mobile': vr.get('visitor_mobile'),
            'purpose': vr.get('purpose'),
            'vehicle_number': vr.get('vehicle_number'),
            'resident_name': rd.get('name'),
            'flat': f"{rd.get('block','')}-{rd.get('floor','')}-{rd.get('flat','')}",
            'visitor_count': vr.get('visitor_count', 1),
        })
    except Exception as e:
        logger.error(f'Error scanning visitor pass: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/pre-approved-passes')
@visitor_login_required
def api_rwa_pre_approved_passes():
    """List pre-approved visitor passes for QR pass display (resident view)."""
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    try:
        q = supabase.table('visitor_requests').select(
            '*, residents(name, block, floor, flat)'
        ).eq('is_pre_approved', True)
        if role == 'resident':
            q = q.eq('resident_id', user['id'])
        res = q.order('created_at', desc=True).limit(20).execute()
        rows = []
        for r in res.data or []:
            rd = r.get('residents') or {}
            rows.append({
                'id': r['id'], 'visitor_name': r.get('visitor_name'),
                'visitor_mobile': r.get('visitor_mobile'), 'purpose': r.get('purpose'),
                'vehicle_number': r.get('vehicle_number'), 'status': r.get('status'),
                'resident_name': rd.get('name'),
                'flat': f"{rd.get('block','')}-{rd.get('floor','')}-{rd.get('flat','')}",
                'created_at': r.get('created_at'),
            })
        return jsonify(rows)
    except Exception as e:
        logger.error(f'Error fetching pre-approved passes: {e}')
        return jsonify([]), 500


# ============================================================
# RWA MODULE: Prime Tier (Phase 3)
# ============================================================

# --- Complaints ---

@app.route('/api/rwa/complaints', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_complaints():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if request.method == 'GET':
        try:
            q = supabase.table('complaints').select('*, residents(name, block, floor, flat)')
            if role == 'resident':
                q = q.eq('resident_id', user['id'])
            res = q.order('created_at', desc=True).execute()
            rows = []
            for r in res.data or []:
                rd = r.get('residents') or {}
                rows.append({
                    'id': r['id'], 'resident_id': r['resident_id'],
                    'resident_name': rd.get('name'), 'block': rd.get('block'),
                    'floor': rd.get('floor'), 'flat': rd.get('flat'),
                    'category': r.get('category'), 'description': r.get('description'),
                    'photo_url': r.get('photo_url'), 'status': r.get('status'),
                    'assigned_to': r.get('assigned_to'), 'created_at': r.get('created_at'),
                    'updated_at': r.get('updated_at')
                })
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching complaints: {e}')
            return jsonify([]), 500
    else:
        body = request.get_json() or {}
        if not body.get('description'):
            return jsonify({'error': 'description is required'}), 400
        if role != 'resident':
            return jsonify({'error': 'Only residents can create complaints'}), 403
        try:
            row = {
                'resident_id': user['id'],
                'category': body.get('category', ''),
                'description': body['description'],
                'photo_url': body.get('photo_url', ''),
            }
            res = supabase.table('complaints').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error creating complaint: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/complaints/<complaint_id>', methods=['PATCH'])
@visitor_login_required
def api_rwa_complaints_patch(complaint_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    allowed = {}
    if 'status' in body and body['status'] in ('open', 'in_progress', 'resolved', 'closed'):
        allowed['status'] = body['status']
    if 'assigned_to' in body:
        allowed['assigned_to'] = body['assigned_to']
    if not allowed:
        return jsonify({'error': 'Nothing to update'}), 400
    allowed['updated_at'] = now_ist().isoformat()
    try:
        supabase.table('complaints').update(allowed).eq('id', complaint_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error updating complaint: {e}')
        return jsonify({'error': str(e)}), 500


# --- Amenities ---

@app.route('/api/rwa/amenities', methods=['GET', 'POST', 'DELETE'])
@visitor_login_required
def api_rwa_amenities():
    if not supabase:
        return jsonify([]), 500
    if request.method == 'GET':
        try:
            res = supabase.table('amenities').select('*').eq('active', True).order('name').execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching amenities: {e}')
            return jsonify([]), 500
    elif request.method == 'DELETE':
        user, role = _get_rwa_session_user()
        if role not in ('admin', 'manager'):
            return jsonify({'error': 'Admin only'}), 403
        amenity_id = request.args.get('id')
        if not amenity_id:
            return jsonify({'error': 'id required'}), 400
        try:
            supabase.table('amenities').update({'active': False}).eq('id', amenity_id).execute()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        user, role = _get_rwa_session_user()
        if role not in ('admin', 'manager'):
            return jsonify({'error': 'Admin only'}), 403
        body = request.get_json() or {}
        if not body.get('name'):
            return jsonify({'error': 'name is required'}), 400
        try:
            res = supabase.table('amenities').insert({
                'name': body['name'], 'description': body.get('description', '')
            }).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/amenity-bookings', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_amenity_bookings():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if request.method == 'GET':
        try:
            q = supabase.table('amenity_bookings').select('*, amenities(name), residents(name)')
            if role == 'resident':
                q = q.eq('resident_id', user['id'])
            amenity_id = request.args.get('amenity_id')
            if amenity_id:
                q = q.eq('amenity_id', amenity_id)
            res = q.order('booking_date', desc=True).execute()
            rows = []
            for r in res.data or []:
                a = r.get('amenities') or {}
                rd = r.get('residents') or {}
                rows.append({
                    'id': r['id'], 'amenity_id': r['amenity_id'],
                    'amenity_name': a.get('name'), 'resident_name': rd.get('name'),
                    'booking_date': r.get('booking_date'), 'slot': r.get('slot'),
                    'status': r.get('status'), 'created_at': r.get('created_at')
                })
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching bookings: {e}')
            return jsonify([]), 500
    else:
        if role != 'resident':
            return jsonify({'error': 'Only residents can book'}), 403
        body = request.get_json() or {}
        if not body.get('amenity_id') or not body.get('booking_date') or not body.get('slot'):
            return jsonify({'error': 'amenity_id, booking_date, and slot are required'}), 400
        try:
            row = {
                'amenity_id': body['amenity_id'],
                'resident_id': user['id'],
                'booking_date': body['booking_date'],
                'slot': body['slot'],
            }
            res = supabase.table('amenity_bookings').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            err = str(e).lower()
            if 'duplicate' in err or 'unique' in err or 'violates' in err:
                return jsonify({'error': 'Slot already booked for this date'}), 409
            logger.error(f'Error booking amenity: {e}')
            return jsonify({'error': str(e)}), 500


# --- Notices ---

@app.route('/api/rwa/notices', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_notices():
    if not supabase:
        return jsonify([]), 500
    if request.method == 'GET':
        try:
            user, role = _get_rwa_session_user()
            res = supabase.table('notices').select('*').order('pinned', desc=True).order('created_at', desc=True).execute()
            rows = res.data or []
            # Filter by scope for residents
            if role == 'resident' and user:
                ub, uf = user.get('block', ''), user.get('floor', '')
                filtered = []
                for n in rows:
                    scope = n.get('target_scope', 'all')
                    if scope == 'all':
                        filtered.append(n)
                    elif scope == 'block' and n.get('target_value') == ub:
                        filtered.append(n)
                    elif scope == 'floor' and n.get('target_value') == uf:
                        filtered.append(n)
                rows = filtered
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching notices: {e}')
            return jsonify([]), 500
    else:
        user, role = _get_rwa_session_user()
        if role not in ('admin', 'manager', 'security'):
            return jsonify({'error': 'Admin/security only'}), 403
        body = request.get_json() or {}
        if not body.get('title') or not body.get('body'):
            return jsonify({'error': 'title and body are required'}), 400
        try:
            row = {
                'title': body['title'],
                'body': body['body'],
                'target_scope': body.get('target_scope', 'all'),
                'target_value': body.get('target_value', ''),
                'posted_by': user.get('name', '') or user.get('email', ''),
                'pinned': body.get('pinned', False),
            }
            res = supabase.table('notices').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error creating notice: {e}')
            return jsonify({'error': str(e)}), 500


# --- Home Planner ---

@app.route('/api/rwa/home-planner', methods=['GET', 'POST', 'PATCH'])
@visitor_login_required
def api_rwa_home_planner():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if role != 'resident':
        return jsonify({'error': 'Resident only'}), 403
    if request.method == 'GET':
        try:
            res = supabase.table('home_planner_tasks').select('*').eq(
                'resident_id', user['id']).order('done').order('due_date').execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching planner: {e}')
            return jsonify([]), 500
    elif request.method == 'POST':
        body = request.get_json() or {}
        if not body.get('title'):
            return jsonify({'error': 'title is required'}), 400
        try:
            res = supabase.table('home_planner_tasks').insert({
                'resident_id': user['id'],
                'title': body['title'],
                'due_date': body.get('due_date'),
            }).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        task_id = request.args.get('id')
        if not task_id:
            return jsonify({'error': 'id required'}), 400
        body = request.get_json() or {}
        allowed = {k: v for k, v in body.items() if k in ('title', 'due_date', 'done')}
        if not allowed:
            return jsonify({'error': 'Nothing to update'}), 400
        try:
            supabase.table('home_planner_tasks').update(allowed).eq('id', task_id).execute()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


# --- Parking ---

@app.route('/api/rwa/parking', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_parking():
    if not supabase:
        return jsonify([]), 500
    if request.method == 'GET':
        try:
            res = supabase.table('parking_slots').select('*, residents(name, mobile)').order('slot_number').execute()
            rows = []
            for r in res.data or []:
                rd = r.get('residents') or {}
                rows.append({
                    'id': r['id'], 'slot_number': r['slot_number'],
                    'owner_name': rd.get('name'), 'owner_mobile': rd.get('mobile'),
                    'status': r['status']
                })
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching parking: {e}')
            return jsonify([]), 500
    else:
        user, role = _get_rwa_session_user()
        if role not in ('admin', 'manager'):
            return jsonify({'error': 'Admin only'}), 403
        body = request.get_json() or {}
        if not body.get('slot_number'):
            return jsonify({'error': 'slot_number is required'}), 400
        try:
            row = {
                'slot_number': body['slot_number'],
                'status': body.get('status', 'owned'),
            }
            if body.get('owner_resident_id'):
                row['owner_resident_id'] = body['owner_resident_id']
            res = supabase.table('parking_slots').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/parking/rent', methods=['POST'])
@visitor_login_required
def api_rwa_parking_rent():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role != 'resident':
        return jsonify({'error': 'Resident only'}), 403
    body = request.get_json() or {}
    if not body.get('slot_id') or not body.get('start_date'):
        return jsonify({'error': 'slot_id and start_date are required'}), 400
    try:
        res = supabase.table('parking_rentals').insert({
            'slot_id': body['slot_id'],
            'renter_resident_id': user['id'],
            'start_date': body['start_date'],
            'end_date': body.get('end_date'),
        }).execute()
        supabase.table('parking_slots').update({'status': 'rented'}).eq('id', body['slot_id']).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- SOS ---

@app.route('/api/rwa/sos', methods=['POST'])
@visitor_login_required
def api_rwa_sos_create():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role != 'resident':
        return jsonify({'error': 'Resident only'}), 403
    try:
        res = supabase.table('sos_alerts').insert({
            'resident_id': user['id'],
            'triggered_at': now_ist().isoformat(),
        }).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error creating SOS: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/sos/active')
@visitor_login_required
def api_rwa_sos_active():
    if not supabase:
        return jsonify([]), 500
    try:
        res = supabase.table('sos_alerts').select('*, residents(name, mobile, block, floor, flat)').is_(
            'acknowledged_at', 'null').order('triggered_at', desc=True).execute()
        rows = []
        for r in res.data or []:
            rd = r.get('residents') or {}
            rows.append({
                'id': r['id'], 'resident_id': r['resident_id'],
                'resident_name': rd.get('name'), 'mobile': rd.get('mobile'),
                'block': rd.get('block'), 'floor': rd.get('floor'), 'flat': rd.get('flat'),
                'triggered_at': r.get('triggered_at'), 'notes': r.get('notes')
            })
        return jsonify(rows)
    except Exception as e:
        logger.error(f'Error fetching active SOS: {e}')
        return jsonify([]), 500


@app.route('/api/rwa/sos/<sos_id>/acknowledge', methods=['PATCH'])
@visitor_login_required
def api_rwa_sos_acknowledge(sos_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role not in ('security', 'admin', 'manager'):
        return jsonify({'error': 'Security/admin only'}), 403
    body = request.get_json() or {}
    try:
        supabase.table('sos_alerts').update({
            'acknowledged_by': user.get('id'),
            'acknowledged_at': now_ist().isoformat(),
            'notes': body.get('notes', '')
        }).eq('id', sos_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error acknowledging SOS: {e}')
        return jsonify({'error': str(e)}), 500


# --- e-Intercom (v1: call request ping) ---

@app.route('/api/rwa/intercom', methods=['POST', 'GET'])
@visitor_login_required
def api_rwa_intercom():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if request.method == 'GET':
        try:
            q = supabase.table('intercom_calls').select('*').order('created_at', desc=True).limit(20)
            if role == 'resident':
                q = q.eq('caller_id', user['id']).or_(f'target_type.eq.security,target_type.eq.gate')
            res = q.execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching intercom calls: {e}')
            return jsonify([]), 500
    else:
        body = request.get_json() or {}
        if not body.get('target_type'):
            return jsonify({'error': 'target_type is required'}), 400
        try:
            row = {
                'caller_id': user['id'],
                'caller_type': 'resident' if role == 'resident' else 'security',
                'target_type': body['target_type'],
                'target_id': body.get('target_id'),
                'status': 'ringing',
            }
            res = supabase.table('intercom_calls').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error creating intercom call: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/intercom/<call_id>/answer', methods=['PATCH'])
@visitor_login_required
def api_rwa_intercom_answer(call_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        supabase.table('intercom_calls').update({
            'status': 'answered',
            'answered_at': now_ist().isoformat()
        }).eq('id', call_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# RWA MODULE: Elite Tier (Phase 4)
# ============================================================

# --- Patrol ---

@app.route('/api/rwa/patrol/checkpoints', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_patrol_checkpoints():
    if not supabase:
        return jsonify([]), 500
    if request.method == 'GET':
        try:
            res = supabase.table('patrol_checkpoints').select('*').eq('active', True).order('name').execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching checkpoints: {e}')
            return jsonify([]), 500
    else:
        user, role = _get_rwa_session_user()
        if role not in ('admin', 'manager'):
            return jsonify({'error': 'Admin only'}), 403
        body = request.get_json() or {}
        if not body.get('name'):
            return jsonify({'error': 'name is required'}), 400
        try:
            res = supabase.table('patrol_checkpoints').insert({
                'name': body['name'],
                'qr_code': body.get('qr_code', ''),
            }).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/patrol/log', methods=['POST', 'GET'])
@visitor_login_required
def api_rwa_patrol_log():
    if not supabase:
        return jsonify([]), 500
    if request.method == 'GET':
        try:
            res = supabase.table('patrol_logs').select(
                '*, patrol_checkpoints(name), security_users(name)'
            ).order('scanned_at', desc=True).limit(100).execute()
            rows = []
            for r in res.data or []:
                cp = r.get('patrol_checkpoints') or {}
                sec = r.get('security_users') or {}
                rows.append({
                    'id': r['id'], 'checkpoint_id': r['checkpoint_id'],
                    'checkpoint_name': cp.get('name'), 'security_name': sec.get('name'),
                    'scanned_at': r.get('scanned_at'), 'notes': r.get('notes')
                })
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching patrol logs: {e}')
            return jsonify([]), 500
    else:
        user, role = _get_rwa_session_user()
        if role not in ('security', 'admin', 'manager'):
            return jsonify({'error': 'Security only'}), 403
        body = request.get_json() or {}
        if not body.get('checkpoint_id'):
            return jsonify({'error': 'checkpoint_id is required'}), 400
        try:
            res = supabase.table('patrol_logs').insert({
                'checkpoint_id': body['checkpoint_id'],
                'security_id': user.get('id'),
                'notes': body.get('notes', ''),
            }).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error logging patrol: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/patrol/checkpoints/<cp_id>/qr')
@visitor_login_required
def api_rwa_patrol_checkpoint_qr(cp_id):
    """Generate a QR code image for a patrol checkpoint.
    The QR encodes a JSON payload with the checkpoint ID and name.
    Print and laminate at the physical checkpoint location."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        res = supabase.table('patrol_checkpoints').select('*').eq('id', cp_id).execute()
        if not res.data:
            return jsonify({'error': 'Checkpoint not found'}), 404
        cp = res.data[0]

        import qrcode as _qrcode
        import io as _io
        import json as _json

        qr_payload = _json.dumps({
            'type': 'rwa_patrol_checkpoint',
            'id': cp['id'],
            'name': cp.get('name', ''),
            'qr_code': cp.get('qr_code', ''),
        })

        qr = _qrcode.QRCode(version=1, error_correction=_qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(qr_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = _io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)

        from flask import send_file as _send_file
        return _send_file(buf, mimetype='image/png')
    except Exception as e:
        logger.error(f'Error generating patrol QR: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/patrol/scan', methods=['POST'])
@visitor_login_required
def api_rwa_patrol_scan():
    """Security scans a patrol checkpoint QR code.
    Parses the QR payload, verifies the checkpoint exists, and logs the patrol scan."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role not in ('security', 'admin', 'manager'):
        return jsonify({'error': 'Security only'}), 403
    body = request.get_json() or {}
    payload = body.get('payload')
    if not payload:
        return jsonify({'error': 'payload is required'}), 400

    import json as _json
    try:
        data = _json.loads(payload) if isinstance(payload, str) else payload
    except Exception:
        return jsonify({'error': 'Invalid QR payload'}), 400

    if data.get('type') != 'rwa_patrol_checkpoint':
        return jsonify({'error': 'Not a patrol checkpoint QR'}), 400

    checkpoint_id = data.get('id')
    if not checkpoint_id:
        return jsonify({'error': 'No checkpoint ID in QR'}), 400

    try:
        cp_res = supabase.table('patrol_checkpoints').select('*').eq('id', checkpoint_id).execute()
        if not cp_res.data:
            return jsonify({'error': 'Checkpoint not found'}), 404
        cp = cp_res.data[0]

        log_res = supabase.table('patrol_logs').insert({
            'checkpoint_id': checkpoint_id,
            'security_id': user.get('id'),
            'scanned_at': now_ist().isoformat(),
            'notes': body.get('notes', ''),
        }).execute()

        return jsonify({
            'success': True,
            'checkpoint_name': cp.get('name', ''),
            'scanned_at': now_ist().isoformat(),
            'log_id': log_res.data[0]['id'] if log_res.data else None,
        })
    except Exception as e:
        logger.error(f'Error scanning patrol QR: {e}')
        return jsonify({'error': str(e)}), 500


# --- Maintenance Invoices ---

@app.route('/api/rwa/invoices', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_invoices():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if request.method == 'GET':
        try:
            q = supabase.table('rwa_invoices').select('*, flats(block, floor, flat_number), residents(name, mobile)')
            if role == 'resident':
                q = q.eq('resident_id', user['id'])
            res = q.order('created_at', desc=True).execute()
            rows = []
            for r in res.data or []:
                f = r.get('flats') or {}
                rd = r.get('residents') or {}
                rows.append({
                    'id': r['id'], 'invoice_number': r['invoice_number'],
                    'billing_month': r.get('billing_month'), 'amount': r.get('amount'),
                    'due_date': r.get('due_date'), 'status': r.get('status'),
                    'flat': f"{f.get('block','')}-{f.get('floor','')}-{f.get('flat_number','')}" if f else '-',
                    'resident_name': rd.get('name'), 'resident_mobile': rd.get('mobile'),
                    'created_at': r.get('created_at')
                })
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching RWA invoices: {e}')
            return jsonify([]), 500
    else:
        if role not in ('admin', 'manager'):
            return jsonify({'error': 'Admin only'}), 403
        body = request.get_json() or {}
        if not body.get('billing_month') or body.get('amount') is None:
            return jsonify({'error': 'billing_month and amount are required'}), 400
        try:
            import uuid as _uuid
            invoice_number = f'RWA-{body["billing_month"].replace("-", "")}-{_uuid.uuid4().hex[:6].upper()}'
            row = {
                'invoice_number': invoice_number,
                'billing_month': body['billing_month'],
                'amount': body['amount'],
                'due_date': body.get('due_date'),
                'status': 'unpaid',
            }
            if body.get('flat_id'):
                row['flat_id'] = body['flat_id']
            if body.get('resident_id'):
                row['resident_id'] = body['resident_id']
            res = supabase.table('rwa_invoices').insert(row).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None, 'invoice_number': invoice_number})
        except Exception as e:
            logger.error(f'Error creating invoice: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/invoices/<invoice_id>', methods=['PATCH'])
@visitor_login_required
def api_rwa_invoices_patch(invoice_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role not in ('admin', 'manager'):
        return jsonify({'error': 'Admin only'}), 403
    body = request.get_json() or {}
    allowed = {k: v for k, v in body.items() if k in ('amount', 'due_date', 'status')}
    if not allowed:
        return jsonify({'error': 'Nothing to update'}), 400
    try:
        supabase.table('rwa_invoices').update(allowed).eq('id', invoice_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Payments ---

@app.route('/api/rwa/payments', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_payments():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if request.method == 'GET':
        try:
            q = supabase.table('rwa_payments').select('*, rwa_invoices(invoice_number, billing_month, resident_id)')
            if role == 'resident':
                q = q.filter('rwa_invoices.resident_id', 'eq', user['id'])
            res = q.order('created_at', desc=True).execute()
            rows = []
            for r in res.data or []:
                inv = r.get('rwa_invoices') or {}
                rows.append({
                    'id': r['id'], 'invoice_id': r['invoice_id'],
                    'invoice_number': inv.get('invoice_number'), 'billing_month': inv.get('billing_month'),
                    'amount': r.get('amount'), 'method': r.get('method'),
                    'status': r.get('status'), 'razorpay_payment_id': r.get('razorpay_payment_id'),
                    'created_at': r.get('created_at')
                })
            return jsonify(rows)
        except Exception as e:
            logger.error(f'Error fetching payments: {e}')
            return jsonify([]), 500
    else:
        body = request.get_json() or {}
        if not body.get('invoice_id') or body.get('amount') is None:
            return jsonify({'error': 'invoice_id and amount are required'}), 400
        try:
            row = {
                'invoice_id': body['invoice_id'],
                'amount': body['amount'],
                'method': body.get('method', 'manual'),
                'status': body.get('status', 'success'),
            }
            if body.get('razorpay_order_id'):
                row['razorpay_order_id'] = body['razorpay_order_id']
            if body.get('razorpay_payment_id'):
                row['razorpay_payment_id'] = body['razorpay_payment_id']
            res = supabase.table('rwa_payments').insert(row).execute()
            if body.get('status', 'success') == 'success':
                supabase.table('rwa_invoices').update({'status': 'paid'}).eq('id', body['invoice_id']).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error recording payment: {e}')
            return jsonify({'error': str(e)}), 500


# --- Razorpay order creation (stub) ---

@app.route('/api/rwa/razorpay/create-order', methods=['POST'])
@visitor_login_required
def api_rwa_razorpay_create_order():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    if not body.get('invoice_id'):
        return jsonify({'error': 'invoice_id is required'}), 400
    try:
        inv_res = supabase.table('rwa_invoices').select('*').eq('id', body['invoice_id']).execute()
        if not inv_res.data:
            return jsonify({'error': 'Invoice not found'}), 404
        inv = inv_res.data[0]
        amount_paise = int(float(inv['amount']) * 100)
        # TODO: integrate actual Razorpay SDK when keys are available
        import uuid as _uuid
        order_id = f'order_{_uuid.uuid4().hex[:16]}'
        supabase.table('rwa_payments').insert({
            'invoice_id': body['invoice_id'],
            'amount': inv['amount'],
            'method': 'razorpay',
            'razorpay_order_id': order_id,
            'status': 'pending',
        }).execute()
        return jsonify({
            'order_id': order_id,
            'amount': amount_paise,
            'currency': 'INR',
            'invoice_number': inv['invoice_number'],
            'note': 'Razorpay integration pending — set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET env vars'
        })
    except Exception as e:
        logger.error(f'Error creating Razorpay order: {e}')
        return jsonify({'error': str(e)}), 500


# --- Vendor Ledger ---

@app.route('/api/rwa/vendor-ledger', methods=['GET', 'POST'])
@visitor_login_required
def api_rwa_vendor_ledger():
    if not supabase:
        return jsonify([]), 500
    user, role = _get_rwa_session_user()
    if request.method == 'GET':
        try:
            res = supabase.table('rwa_vendor_ledger').select('*').order('created_at', desc=True).execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching vendor ledger: {e}')
            return jsonify([]), 500
    else:
        if role not in ('admin', 'manager'):
            return jsonify({'error': 'Admin only'}), 403
        body = request.get_json() or {}
        if not body.get('vendor_name') or body.get('invoice_amount') is None:
            return jsonify({'error': 'vendor_name and invoice_amount are required'}), 400
        try:
            paid = float(body.get('paid_amount', 0) or 0)
            total = float(body['invoice_amount'])
            status = 'paid' if paid >= total else ('partially_paid' if paid > 0 else 'unpaid')
            res = supabase.table('rwa_vendor_ledger').insert({
                'vendor_name': body['vendor_name'],
                'category': body.get('category', ''),
                'invoice_amount': total,
                'paid_amount': paid,
                'status': status,
                'notes': body.get('notes', ''),
            }).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/api/rwa/vendor-ledger/<entry_id>', methods=['PATCH'])
@visitor_login_required
def api_rwa_vendor_ledger_patch(entry_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user, role = _get_rwa_session_user()
    if role not in ('admin', 'manager'):
        return jsonify({'error': 'Admin only'}), 403
    body = request.get_json() or {}
    allowed = {k: v for k, v in body.items() if k in ('paid_amount', 'status', 'notes')}
    if not allowed:
        return jsonify({'error': 'Nothing to update'}), 400
    try:
        if 'paid_amount' in allowed:
            paid = float(allowed['paid_amount'])
            cur = supabase.table('rwa_vendor_ledger').select('invoice_amount').eq('id', entry_id).execute()
            if cur.data:
                total = float(cur.data[0]['invoice_amount'])
                allowed['status'] = 'paid' if paid >= total else ('partially_paid' if paid > 0 else 'unpaid')
        supabase.table('rwa_vendor_ledger').update(allowed).eq('id', entry_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Reports ---

@app.route('/api/rwa/reports/summary')
@requires_role('admin', 'manager')
def api_rwa_reports_summary():
    if not supabase:
        return jsonify({}), 500
    try:
        inv_res = supabase.table('rwa_invoices').select('amount, status').execute()
        invoices = inv_res.data or []
        total_billed = sum(float(i.get('amount', 0)) for i in invoices)
        total_paid = sum(float(i.get('amount', 0)) for i in invoices if i.get('status') == 'paid')
        total_unpaid = total_billed - total_paid

        pay_res = supabase.table('rwa_payments').select('amount, status').execute()
        payments = pay_res.data or []
        total_collected = sum(float(p.get('amount', 0)) for p in payments if p.get('status') == 'success')

        vl_res = supabase.table('rwa_vendor_ledger').select('invoice_amount, paid_amount, status').execute()
        vl = vl_res.data or []
        vendor_total = sum(float(v.get('invoice_amount', 0)) for v in vl)
        vendor_paid = sum(float(v.get('paid_amount', 0)) for v in vl)

        comp_res = supabase.table('complaints').select('status').execute()
        complaints = comp_res.data or []
        open_complaints = len([c for c in complaints if c.get('status') in ('open', 'in_progress')])

        patrol_res = supabase.table('patrol_logs').select('scanned_at').execute()
        patrol_logs = patrol_res.data or []
        cutoff = (now_ist() - timedelta(hours=24)).isoformat()
        recent_patrols = len([p for p in patrol_logs if (p.get('scanned_at') or '') > cutoff])

        return jsonify({
            'invoices': {
                'total_billed': total_billed,
                'total_paid': total_paid,
                'total_unpaid': total_unpaid,
                'count': len(invoices),
            },
            'payments': {
                'total_collected': total_collected,
                'count': len(payments),
            },
            'vendor_ledger': {
                'total_invoiced': vendor_total,
                'total_paid': vendor_paid,
                'outstanding': vendor_total - vendor_paid,
            },
            'complaints': {
                'open': open_complaints,
                'total': len(complaints),
            },
            'patrol': {
                'last_24h': recent_patrols,
                'total': len(patrol_logs),
            }
        })
    except Exception as e:
        logger.error(f'Error generating report: {e}')
        return jsonify({}), 500


# ============================================================
# RERA Quarterly Progress Report (Form B) Module
# ============================================================

RERA_DEFAULT_THRESHOLDS = {
    'red': 0, 'yellow': 40, 'blue': 75, 'green': 100
}


def _rera_current_quarter():
    """Return (quarter_label, start_date, end_date, filing_deadline) for the current quarter."""
    now = now_ist()
    month = now.month
    year = now.year
    if month <= 3:
        q_label = f'{year}-Q1'
        q_start = date(year, 1, 1)
        q_end = date(year, 3, 31)
    elif month <= 6:
        q_label = f'{year}-Q2'
        q_start = date(year, 4, 1)
        q_end = date(year, 6, 30)
    elif month <= 9:
        q_label = f'{year}-Q3'
        q_start = date(year, 7, 1)
        q_end = date(year, 9, 30)
    else:
        q_label = f'{year}-Q4'
        q_start = date(year, 10, 1)
        q_end = date(year, 12, 31)
    filing_deadline = q_end + timedelta(days=15)
    return q_label, q_start, q_end, filing_deadline


def _rera_get_thresholds(venture_id):
    """Fetch color→pct thresholds, with per-venture overrides merging onto defaults."""
    thresholds = dict(RERA_DEFAULT_THRESHOLDS)
    if not supabase:
        return thresholds
    try:
        res = supabase.table('rera_color_thresholds').select('*').execute()
        for row in res.data or []:
            v_id = row.get('venture_id')
            if v_id is None:
                thresholds[row['color']] = float(row['pct_value'])
        # Venture-specific overrides
        for row in res.data or []:
            if row.get('venture_id') == venture_id and row.get('work_item') is None:
                thresholds[row['color']] = float(row['pct_value'])
    except Exception as e:
        logger.error(f'Error fetching RERA thresholds: {e}')
    return thresholds


def _rera_compute_progress(venture_id, thresholds):
    """Compute % completion per block/floor from cell_data colors."""
    if not supabase:
        return {'blocks': [], 'overall_pct': 0}
    try:
        res = supabase.table('cell_data').select('*').execute()
        block_stats = {}
        total_weighted = 0
        total_cells = 0
        for row in (res.data or []):
            d = row.get('data') or {}
            if d.get('venture_id') != venture_id:
                continue
            block = d.get('block', 'Unknown')
            floor = d.get('floor', 'Unknown')
            color = d.get('color', 'red')
            pct = thresholds.get(color, 0)
            key = block
            if key not in block_stats:
                block_stats[key] = {'block': block, 'floors': {}, 'total_pct': 0, 'cell_count': 0}
            floor_key = floor
            if floor_key not in block_stats[key]['floors']:
                block_stats[key]['floors'][floor_key] = {'floor': floor, 'total_pct': 0, 'cell_count': 0}
            block_stats[key]['floors'][floor_key]['total_pct'] += pct
            block_stats[key]['floors'][floor_key]['cell_count'] += 1
            block_stats[key]['total_pct'] += pct
            block_stats[key]['cell_count'] += 1
            total_weighted += pct
            total_cells += 1
        blocks = []
        for block_name, stats in sorted(block_stats.items()):
            block_pct = round(stats['total_pct'] / stats['cell_count'], 1) if stats['cell_count'] else 0
            floors = []
            for floor_name, fs in sorted(stats['floors'].items()):
                floor_pct = round(fs['total_pct'] / fs['cell_count'], 1) if fs['cell_count'] else 0
                floors.append({
                    'floor': fs['floor'],
                    'cell_count': fs['cell_count'],
                    'pct_complete': floor_pct
                })
            blocks.append({
                'block': block_name,
                'cell_count': stats['cell_count'],
                'pct_complete': block_pct,
                'floors': floors
            })
        overall = round(total_weighted / total_cells, 1) if total_cells else 0
        return {'blocks': blocks, 'overall_pct': overall}
    except Exception as e:
        logger.error(f'Error computing RERA progress: {e}')
        return {'blocks': [], 'overall_pct': 0}


def _rera_compute_financials(venture_id):
    """Compute funds collected, utilized, and escrow balance."""
    collected = 0.0
    utilized = 0.0
    if not supabase:
        return {'collected': 0, 'utilized': 0, 'escrow_balance': 0}
    try:
        # Funds collected from invoices
        inv_res = supabase.table('invoices').select('*').execute()
        for inv in inv_res.data or []:
            d = inv.get('data') or {}
            v_match = d.get('venture_id') == venture_id or inv.get('venture_id') == venture_id
            if not v_match:
                continue
            status = (d.get('status') or inv.get('status') or '').lower()
            amt = float(d.get('amount') or inv.get('amount') or 0)
            if status in ('paid', 'received', 'completed'):
                collected += amt
    except Exception as e:
        logger.error(f'Error fetching invoices for RERA: {e}')
    try:
        # Funds utilized from expenditures
        exp_res = supabase.table('expenditures').select('*').eq('venture_id', venture_id).execute()
        for exp in exp_res.data or []:
            d = exp.get('data') or {}
            utilized += float(d.get('amount', 0))
    except Exception as e:
        logger.error(f'Error fetching expenditures for RERA: {e}')
    return {
        'collected': round(collected, 2),
        'utilized': round(utilized, 2),
        'escrow_balance': round(collected - utilized, 2)
    }


def _rera_compute_milestones(venture_id):
    """Extract milestone dates from cell_data timeline entries."""
    milestones = []
    if not supabase:
        return milestones
    try:
        res = supabase.table('cell_data').select('*').execute()
        seen = {}
        for row in (res.data or []):
            d = row.get('data') or {}
            if d.get('venture_id') != venture_id:
                continue
            block = d.get('block', 'Unknown')
            work_item = d.get('work_item', '')
            timeline = d.get('timeline') or []
            for entry in timeline:
                if entry.get('color') == 'green':
                    key = f'{block}|{work_item}'
                    ev_date = entry.get('date', '')
                    if key not in seen or ev_date < seen[key]['actual_date']:
                        seen[key] = {
                            'block': block,
                            'work_item': work_item,
                            'actual_date': ev_date,
                            'changed_by': entry.get('changed_by', '')
                        }
        milestones = sorted(seen.values(), key=lambda m: (m['block'], m['work_item']))
    except Exception as e:
        logger.error(f'Error computing RERA milestones: {e}')
    return milestones


def _rera_unit_status(venture_id):
    """Compute unit status: total/sold/available by category."""
    if not supabase:
        return {'total': 0, 'sold': 0, 'available': 0, 'has_data': False}
    try:
        res = supabase.table('cell_data').select('*').execute()
        flats = set()
        for row in (res.data or []):
            d = row.get('data') or {}
            if d.get('venture_id') != venture_id:
                continue
            cell_id = row.get('id', '')
            parts = cell_id.split('_item_')
            if parts:
                flats.add(parts[0])
        total = len(flats)
        return {'total': total, 'sold': 0, 'available': total, 'has_data': total > 0}
    except Exception as e:
        logger.error(f'Error computing RERA unit status: {e}')
        return {'total': 0, 'sold': 0, 'available': 0, 'has_data': False}


def _rera_compliance_checklist(venture_id, progress, financials, units, milestones, approvals):
    """Build Form B compliance checklist with status indicators."""
    checklist = []
    # Construction progress
    has_progress = progress['overall_pct'] > 0 or len(progress['blocks']) > 0
    checklist.append({
        'field': 'Construction Progress (% per tower/block)',
        'status': 'green' if has_progress and progress['overall_pct'] > 0 else ('yellow' if has_progress else 'red'),
        'source': 'cell_data',
        'detail': f"{len(progress['blocks'])} blocks, {progress['overall_pct']}% overall"
    })
    # Funds collected
    has_collected = financials['collected'] > 0
    checklist.append({
        'field': 'Funds Collected',
        'status': 'green' if has_collected else 'red',
        'source': 'invoices (status=paid)',
        'detail': f"₹{financials['collected']:,.0f}"
    })
    # Funds utilized
    has_utilized = financials['utilized'] > 0
    checklist.append({
        'field': 'Funds Utilized',
        'status': 'green' if has_utilized else 'red',
        'source': 'expenditures',
        'detail': f"₹{financials['utilized']:,.0f}"
    })
    # Escrow balance
    checklist.append({
        'field': 'Escrow Balance',
        'status': 'green' if has_collected or has_utilized else 'red',
        'source': 'derived (collected - utilized)',
        'detail': f"₹{financials['escrow_balance']:,.0f}"
    })
    # Unit status
    checklist.append({
        'field': 'Unit Status (total/sold/available)',
        'status': 'green' if units.get('has_data') else 'red',
        'source': 'cell_data (flat count)',
        'detail': f"{units.get('total', 0)} units" + ("" if units.get('has_data') else " — no sales data source")
    })
    # Milestones
    checklist.append({
        'field': 'Milestone Status (key dates)',
        'status': 'green' if len(milestones) > 0 else 'red',
        'source': 'cell_data.timeline[]',
        'detail': f"{len(milestones)} milestones recorded"
    })
    # Statutory approvals
    checklist.append({
        'field': 'Statutory Approvals / Renewals',
        'status': 'green' if len(approvals) > 0 else 'red',
        'source': 'rera_statutory_approvals',
        'detail': f"{len(approvals)} approvals on record"
    })
    return checklist


@app.route('/rera')
@login_required
def rera_page():
    return render_template('rera.html')


@app.route('/api/rera/readiness/<venture_id>')
@requires_role('manager', 'admin')
def api_rera_readiness(venture_id):
    """RERA Readiness Dashboard: computed %, financials, compliance checklist."""
    try:
        thresholds = _rera_get_thresholds(venture_id)
        progress = _rera_compute_progress(venture_id, thresholds)
        financials = _rera_compute_financials(venture_id)
        units = _rera_unit_status(venture_id)
        milestones = _rera_compute_milestones(venture_id)
        # Statutory approvals
        approvals = []
        if supabase:
            try:
                ap_res = supabase.table('rera_statutory_approvals').select('*').eq('venture_id', venture_id).execute()
                approvals = ap_res.data or []
            except Exception:
                pass
        checklist = _rera_compliance_checklist(venture_id, progress, financials, units, milestones, approvals)
        q_label, q_start, q_end, filing_deadline = _rera_current_quarter()
        now = now_ist().date()
        days_remaining = (filing_deadline - now).days
        # Check if a report already exists for this quarter
        existing_report = None
        if supabase:
            try:
                rpt_res = supabase.table('rera_quarterly_reports').select('*').eq('venture_id', venture_id).eq('quarter', q_label).execute()
                if rpt_res.data:
                    existing_report = rpt_res.data[0]
            except Exception:
                pass
        return jsonify({
            'venture_id': venture_id,
            'progress': progress,
            'financials': financials,
            'units': units,
            'milestones': milestones,
            'approvals': approvals,
            'checklist': checklist,
            'quarter': {
                'label': q_label,
                'start': str(q_start),
                'end': str(q_end),
                'filing_deadline': str(filing_deadline),
                'days_remaining': days_remaining
            },
            'existing_report': existing_report
        })
    except Exception as e:
        logger.error(f'Error in RERA readiness: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rera/draft/<venture_id>/<quarter>')
@requires_role('manager', 'admin')
def api_rera_draft(venture_id, quarter):
    """Generate a draft Form B report with all computed fields."""
    try:
        thresholds = _rera_get_thresholds(venture_id)
        progress = _rera_compute_progress(venture_id, thresholds)
        financials = _rera_compute_financials(venture_id)
        units = _rera_unit_status(venture_id)
        milestones = _rera_compute_milestones(venture_id)
        approvals = []
        if supabase:
            try:
                ap_res = supabase.table('rera_statutory_approvals').select('*').eq('venture_id', venture_id).execute()
                approvals = ap_res.data or []
            except Exception:
                pass
        # Delays
        delays = []
        if supabase:
            try:
                dl_res = supabase.table('rera_delay_log').select('*').eq('venture_id', venture_id).eq('quarter', quarter).execute()
                delays = dl_res.data or []
            except Exception:
                pass
        # Venture metadata
        venture_name = venture_id
        if supabase:
            try:
                v_res = supabase.table('ventures').select('*').eq('id', venture_id).execute()
                if v_res.data:
                    venture_name = (v_res.data[0].get('data') or {}).get('name') or v_res.data[0].get('name') or venture_id
            except Exception:
                pass
        # Parse quarter dates
        year, q_num = quarter.split('-Q')
        q_num = int(q_num)
        year = int(year)
        if q_num == 1:
            q_start, q_end = date(year, 1, 1), date(year, 3, 31)
        elif q_num == 2:
            q_start, q_end = date(year, 4, 1), date(year, 6, 30)
        elif q_num == 3:
            q_start, q_end = date(year, 7, 1), date(year, 9, 30)
        else:
            q_start, q_end = date(year, 10, 1), date(year, 12, 31)
        filing_deadline = q_end + timedelta(days=15)
        draft = {
            'venture_id': venture_id,
            'venture_name': venture_name,
            'quarter': quarter,
            'quarter_start': str(q_start),
            'quarter_end': str(q_end),
            'filing_deadline': str(filing_deadline),
            'generated_at': now_ist().isoformat(),
            'construction_progress': progress,
            'financial_updates': financials,
            'unit_status': units,
            'milestone_status': milestones,
            'compliance_status': [
                {
                    'approval_name': a.get('approval_name', ''),
                    'issuing_authority': a.get('issuing_authority', ''),
                    'issued_date': str(a.get('issued_date', '')) if a.get('issued_date') else '',
                    'expiry_date': str(a.get('expiry_date', '')) if a.get('expiry_date') else '',
                    'status': a.get('status', 'active'),
                    'remarks': a.get('remarks', '')
                }
                for a in approvals
            ],
            'delays_issues': [
                {
                    'block': d.get('block', ''),
                    'floor': d.get('floor', ''),
                    'work_item': d.get('work_item', ''),
                    'delay_days': d.get('delay_days', 0),
                    'reason': d.get('reason', '')
                }
                for d in delays
            ]
        }
        return jsonify(draft)
    except Exception as e:
        logger.error(f'Error generating RERA draft: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rera/report/submit', methods=['POST'])
@requires_role('manager', 'admin')
def api_rera_report_submit():
    """Submit & lock a quarterly report — creates an immutable snapshot."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    venture_id = body.get('venture_id')
    quarter = body.get('quarter')
    report_data = body.get('report_data')
    if not venture_id or not quarter or not report_data:
        return jsonify({'error': 'venture_id, quarter, and report_data are required'}), 400
    try:
        # Parse quarter dates
        year, q_num = quarter.split('-Q')
        q_num = int(q_num)
        year = int(year)
        if q_num == 1:
            q_start, q_end = date(year, 1, 1), date(year, 3, 31)
        elif q_num == 2:
            q_start, q_end = date(year, 4, 1), date(year, 6, 30)
        elif q_num == 3:
            q_start, q_end = date(year, 7, 1), date(year, 9, 30)
        else:
            q_start, q_end = date(year, 10, 1), date(year, 12, 31)
        filing_deadline = q_end + timedelta(days=15)
        user = session.get('user')
        submitted_by = user.get('email') if isinstance(user, dict) else str(user)
        # Check if already exists
        existing = supabase.table('rera_quarterly_reports').select('*').eq('venture_id', venture_id).eq('quarter', quarter).execute()
        if existing.data:
            existing_row = existing.data[0]
            if existing_row.get('status') in ('locked', 'submitted'):
                return jsonify({'error': 'Report already submitted/locked for this quarter'}), 409
            # Update existing draft → locked
            res = supabase.table('rera_quarterly_reports').update({
                'status': 'locked',
                'report_data': report_data,
                'submitted_by': submitted_by,
                'submitted_at': now_ist().isoformat(),
                'filing_deadline': str(filing_deadline)
            }).eq('id', existing_row['id']).execute()
        else:
            res = supabase.table('rera_quarterly_reports').insert({
                'venture_id': venture_id,
                'quarter': quarter,
                'quarter_start': str(q_start),
                'quarter_end': str(q_end),
                'filing_deadline': str(filing_deadline),
                'status': 'locked',
                'report_data': report_data,
                'submitted_by': submitted_by,
                'submitted_at': now_ist().isoformat()
            }).execute()
        return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
    except Exception as e:
        logger.error(f'Error submitting RERA report: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rera/reports/<venture_id>')
@requires_role('manager', 'admin')
def api_rera_reports_list(venture_id):
    """List all filed/locked quarterly reports for a venture."""
    if not supabase:
        return jsonify([])
    try:
        res = supabase.table('rera_quarterly_reports').select('*').eq('venture_id', venture_id).order('created_at', desc=True).execute()
        return jsonify([{
            'id': r['id'],
            'venture_id': r['venture_id'],
            'quarter': r['quarter'],
            'quarter_start': str(r.get('quarter_start', '')),
            'quarter_end': str(r.get('quarter_end', '')),
            'filing_deadline': str(r.get('filing_deadline', '')),
            'status': r.get('status', 'draft'),
            'submitted_by': r.get('submitted_by', ''),
            'submitted_at': r.get('submitted_at', ''),
            'created_at': r.get('created_at', '')
        } for r in (res.data or [])])
    except Exception as e:
        logger.error(f'Error listing RERA reports: {e}')
        return jsonify([])


@app.route('/api/rera/report/<report_id>')
@requires_role('manager', 'admin')
def api_rera_report_detail(report_id):
    """View a single locked report with full report_data."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        res = supabase.table('rera_quarterly_reports').select('*').eq('id', report_id).execute()
        if not res.data:
            return jsonify({'error': 'Report not found'}), 404
        r = res.data[0]
        return jsonify({
            'id': r['id'],
            'venture_id': r['venture_id'],
            'quarter': r['quarter'],
            'quarter_start': str(r.get('quarter_start', '')),
            'quarter_end': str(r.get('quarter_end', '')),
            'filing_deadline': str(r.get('filing_deadline', '')),
            'status': r.get('status', 'draft'),
            'report_data': r.get('report_data', {}),
            'submitted_by': r.get('submitted_by', ''),
            'submitted_at': r.get('submitted_at', ''),
            'created_at': r.get('created_at', '')
        })
    except Exception as e:
        logger.error(f'Error fetching RERA report: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/rera/approvals', methods=['GET', 'POST'])
@requires_role('admin')
def api_rera_approvals():
    """CRUD for statutory approvals."""
    if not supabase:
        return jsonify([]) if request.method == 'GET' else jsonify({'success': True, 'note': 'read-only local mode'})
    if request.method == 'GET':
        venture_id = request.args.get('venture_id')
        try:
            q = supabase.table('rera_statutory_approvals').select('*')
            if venture_id:
                q = q.eq('venture_id', venture_id)
            res = q.order('created_at', desc=True).execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching RERA approvals: {e}')
            return jsonify([])
    else:
        body = request.get_json() or {}
        required = ['venture_id', 'approval_name']
        for field in required:
            if field not in body or body[field] in (None, ''):
                return jsonify({'error': f'{field} is required'}), 400
        try:
            entry = {
                'venture_id': body['venture_id'],
                'approval_name': body['approval_name'],
                'issuing_authority': body.get('issuing_authority', ''),
                'issued_date': body.get('issued_date', None),
                'expiry_date': body.get('expiry_date', None),
                'status': body.get('status', 'active'),
                'remarks': body.get('remarks', '')
            }
            res = supabase.table('rera_statutory_approvals').insert(entry).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error creating RERA approval: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rera/approval/<approval_id>', methods=['PUT', 'DELETE'])
@requires_role('admin')
def api_rera_approval_modify(approval_id):
    """Update or delete a statutory approval."""
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    if request.method == 'DELETE':
        try:
            supabase.table('rera_statutory_approvals').delete().eq('id', approval_id).execute()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        body = request.get_json() or {}
        allowed = {k: v for k, v in body.items() if k in (
            'approval_name', 'issuing_authority', 'issued_date', 'expiry_date', 'status', 'remarks'
        )}
        try:
            supabase.table('rera_statutory_approvals').update(allowed).eq('id', approval_id).execute()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/api/rera/thresholds', methods=['GET', 'POST'])
@requires_role('admin')
def api_rera_thresholds():
    """Get or set color→pct thresholds."""
    if not supabase:
        if request.method == 'GET':
            return jsonify([{'color': k, 'pct_value': v, 'venture_id': None, 'work_item': None}
                            for k, v in RERA_DEFAULT_THRESHOLDS.items()])
        return jsonify({'success': True, 'note': 'read-only local mode'})
    if request.method == 'GET':
        try:
            res = supabase.table('rera_color_thresholds').select('*').execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching RERA thresholds: {e}')
            return jsonify([])
    else:
        body = request.get_json() or {}
        if isinstance(body, list):
            results = []
            for item in body:
                try:
                    res = supabase.table('rera_color_thresholds').upsert({
                        'venture_id': item.get('venture_id'),
                        'work_item': item.get('work_item'),
                        'color': item['color'],
                        'pct_value': float(item['pct_value'])
                    }).execute()
                    results.append({'success': True})
                except Exception as e:
                    results.append({'error': str(e)})
            return jsonify({'results': results})
        else:
            try:
                res = supabase.table('rera_color_thresholds').upsert({
                    'venture_id': body.get('venture_id'),
                    'work_item': body.get('work_item'),
                    'color': body['color'],
                    'pct_value': float(body['pct_value'])
                }).execute()
                return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
            except Exception as e:
                return jsonify({'error': str(e)}), 500


@app.route('/api/rera/delays', methods=['GET', 'POST'])
@requires_role('manager', 'admin')
def api_rera_delays():
    """Get or create delay log entries."""
    if not supabase:
        return jsonify([]) if request.method == 'GET' else jsonify({'success': True, 'note': 'read-only local mode'})
    if request.method == 'GET':
        venture_id = request.args.get('venture_id')
        quarter = request.args.get('quarter')
        try:
            q = supabase.table('rera_delay_log').select('*')
            if venture_id:
                q = q.eq('venture_id', venture_id)
            if quarter:
                q = q.eq('quarter', quarter)
            res = q.order('created_at', desc=True).execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching RERA delays: {e}')
            return jsonify([])
    else:
        body = request.get_json() or {}
        required = ['venture_id', 'quarter']
        for field in required:
            if field not in body or body[field] in (None, ''):
                return jsonify({'error': f'{field} is required'}), 400
        try:
            entry = {
                'venture_id': body['venture_id'],
                'quarter': body['quarter'],
                'block': body.get('block', ''),
                'floor': body.get('floor', ''),
                'work_item': body.get('work_item', ''),
                'delay_days': int(body.get('delay_days', 0)),
                'reason': body.get('reason', '')
            }
            res = supabase.table('rera_delay_log').insert(entry).execute()
            return jsonify({'success': True, 'id': res.data[0]['id'] if res.data else None})
        except Exception as e:
            logger.error(f'Error creating RERA delay log: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/rera/delay/<delay_id>', methods=['DELETE'])
@requires_role('manager', 'admin')
def api_rera_delay_delete(delay_id):
    """Delete a delay log entry."""
    if not supabase:
        return jsonify({'success': True, 'note': 'read-only local mode'})
    try:
        supabase.table('rera_delay_log').delete().eq('id', delay_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========================
# Inventory Ready — New Routes
# ========================

@app.route('/api/cell/<cell_id>/usage', methods=['POST'])
@requires_role_or_override('supervisor')
def api_cell_usage(cell_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    venture_id = body.get('venture_id')
    material_id = body.get('material_id')
    qty_used = float(body.get('qty_used') or 0)
    qty_wasted = float(body.get('qty_wasted') or 0)
    if not venture_id or not material_id:
        return jsonify({'error': 'venture_id and material_id are required'}), 400
    if qty_used <= 0 and qty_wasted <= 0:
        return jsonify({'error': 'qty_used or qty_wasted must be > 0'}), 400
    allowed = _allowed_ventures(session['user'])
    if venture_id not in allowed:
        return jsonify({'error': 'Forbidden'}), 403
    try:
        result = supabase.rpc('record_cell_usage', {
            'p_cell_id': cell_id,
            'p_venture_id': venture_id,
            'p_block': body.get('block'),
            'p_floor': body.get('floor'),
            'p_flat': body.get('flat'),
            'p_work_item': body.get('work_item'),
            'p_material_id': material_id,
            'p_qty_used': qty_used,
            'p_qty_wasted': qty_wasted,
            'p_wastage_reason': body.get('wastage_reason'),
            'p_entry_date': body.get('entry_date') or date.today().isoformat(),
            'p_created_by': session['user'].get('email')
        }).execute()
        return jsonify(result.data or {'success': True})
    except Exception as e:
        err = str(e)
        if 'Insufficient stock' in err:
            return jsonify({'error': 'Insufficient stock. Ask admin to transfer more stock to this venture, then retry.'}), 400
        logger.error(f'Error recording cell usage: {e}')
        return jsonify({'error': err}), 500


@app.route('/api/cell/<cell_id>/reverse-usage', methods=['POST'])
@requires_role_or_override('supervisor')
def api_cell_reverse_usage(cell_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    usage_id = body.get('usage_id')
    reverse_qty = float(body.get('reverse_qty') or 0)
    if not usage_id or reverse_qty <= 0:
        return jsonify({'error': 'usage_id and reverse_qty > 0 are required'}), 400
    try:
        result = supabase.rpc('reverse_cell_usage', {
            'p_usage_id': usage_id,
            'p_reverse_qty': reverse_qty,
            'p_reason': body.get('reason'),
            'p_created_by': session['user'].get('email')
        }).execute()
        return jsonify(result.data or {'success': True})
    except Exception as e:
        err = str(e)
        if 'Cannot reverse' in err:
            return jsonify({'error': err}), 400
        logger.error(f'Error reversing cell usage: {e}')
        return jsonify({'error': err}), 500


@app.route('/api/cell/<cell_id>/material-usage')
@requires_role_or_override('supervisor')
def api_cell_material_usage(cell_id):
    if not supabase:
        return jsonify({'usage': [], 'reversals': []})
    try:
        usage_res = supabase.table('cell_material_usage').select('*').eq('cell_id', cell_id).order('entry_date', desc=True).execute()
        usage_rows = usage_res.data or []
        reversal_rows = []
        if usage_rows:
            usage_ids = [u['id'] for u in usage_rows]
            for uid in usage_ids:
                rev_res = supabase.table('cell_material_usage_reversals').select('*').eq('usage_id', uid).execute()
                reversal_rows.extend(rev_res.data or [])
        return jsonify({'usage': usage_rows, 'reversals': reversal_rows})
    except Exception as e:
        logger.error(f'Error fetching cell material usage: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/transfer-stock', methods=['POST'])
@requires_role('admin', 'manager')
def api_transfer_stock():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    to_venture = body.get('to_venture_id')
    material_id = body.get('material_id')
    qty = float(body.get('qty') or 0)
    if not to_venture or not material_id or qty <= 0:
        return jsonify({'error': 'to_venture_id, material_id, and qty > 0 are required'}), 400
    allowed = _allowed_ventures(session['user'])
    if to_venture not in allowed:
        return jsonify({'error': 'Forbidden'}), 403
    try:
        result = supabase.rpc('transfer_stock', {
            'p_to_venture_id': to_venture,
            'p_material_id': material_id,
            'p_qty': qty,
            'p_transfer_date': body.get('transfer_date') or date.today().isoformat(),
            'p_created_by': session['user'].get('email')
        }).execute()
        return jsonify(result.data or {'success': True})
    except Exception as e:
        err = str(e)
        if 'Insufficient warehouse stock' in err:
            return jsonify({'error': 'Insufficient warehouse stock for this material'}), 400
        logger.error(f'Error transferring stock: {e}')
        return jsonify({'error': err}), 500


@app.route('/api/stock/projections')
@requires_role_or_override('supervisor')
def api_stock_projections():
    if not supabase:
        return jsonify([])
    venture_id = request.args.get('venture_id')
    if not venture_id:
        return jsonify({'error': 'venture_id is required'}), 400
    allowed = _allowed_ventures(session['user'])
    if venture_id not in allowed and venture_id != 'WAREHOUSE':
        return jsonify({'error': 'Forbidden'}), 403
    try:
        bal_res = supabase.table('stock_balance').select('*').eq('venture_id', venture_id).execute()
        projections = []
        for b in (bal_res.data or []):
            balance = float(b.get('balance', 0))
            total_used = float(b.get('total_used', 0))
            avg_daily = total_used / 30 if total_used > 0 else 0
            eow_balance = balance - (avg_daily * 7)
            projections.append({
                'material_id': b['material_id'],
                'balance': balance,
                'avg_daily_usage': round(avg_daily, 2),
                'projected_eow': round(eow_balance, 2),
                'days_until_empty': round(balance / avg_daily, 1) if avg_daily > 0 else None
            })
        return jsonify(projections)
    except Exception as e:
        logger.error(f'Error fetching projections: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock/venture-usage')
@requires_role_or_override('supervisor')
def api_stock_venture_usage():
    if not supabase:
        return jsonify([])
    venture_id = request.args.get('venture_id')
    if not venture_id:
        return jsonify({'error': 'venture_id is required'}), 400
    allowed = _allowed_ventures(session['user'])
    if venture_id not in allowed:
        return jsonify({'error': 'Forbidden'}), 403
    try:
        q = supabase.table('stock_ledger').select('*').eq('venture_id', venture_id).eq('entry_type', 'OUT')
        block = request.args.get('block')
        floor = request.args.get('floor')
        if block:
            q = q.eq('block', block)
        if floor:
            q = q.eq('floor', floor)
        res = q.execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching venture usage: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock/wastage-report')
@requires_role_or_override('supervisor')
def api_stock_wastage_report():
    if not supabase:
        return jsonify([])
    venture_id = request.args.get('venture_id')
    if not venture_id:
        return jsonify({'error': 'venture_id is required'}), 400
    allowed = _allowed_ventures(session['user'])
    if venture_id not in allowed:
        return jsonify({'error': 'Forbidden'}), 403
    try:
        res = supabase.table('stock_ledger').select('*').eq('venture_id', venture_id).eq('entry_type', 'OUT').eq('is_wastage', True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching wastage report: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/material-budgets', methods=['GET', 'POST'])
@requires_role('admin', 'manager')
def api_material_budgets():
    if not supabase:
        return jsonify([]) if request.method == 'GET' else jsonify({'error': 'Supabase not connected'}), 500
    if request.method == 'GET':
        venture_id = request.args.get('venture_id')
        try:
            q = supabase.table('material_budgets').select('*')
            if venture_id:
                q = q.eq('venture_id', venture_id)
            res = q.execute()
            return jsonify(res.data or [])
        except Exception as e:
            logger.error(f'Error fetching budgets: {e}')
            return jsonify({'error': str(e)}), 500
    else:
        body = request.get_json() or {}
        venture_id = body.get('venture_id')
        material_id = body.get('material_id')
        if not venture_id or not material_id:
            return jsonify({'error': 'venture_id and material_id are required'}), 400
        allowed = _allowed_ventures(session['user'])
        if venture_id not in allowed:
            return jsonify({'error': 'Forbidden'}), 403
        try:
            supabase.table('material_budgets').upsert({
                'venture_id': venture_id,
                'material_id': material_id,
                'budget_qty': float(body.get('budget_qty', 0)),
                'budget_value': float(body.get('budget_value', 0)),
                'alert_threshold_pct': float(body.get('alert_threshold_pct', 80)),
                'updated_at': now_ist().isoformat()
            }, on_conflict='venture_id,material_id').execute()
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f'Error saving budget: {e}')
            return jsonify({'error': str(e)}), 500


@app.route('/api/inventory/alerts')
@requires_role_or_override('supervisor')
def api_inventory_alerts():
    if not supabase:
        return jsonify([])
    try:
        allowed = _allowed_ventures(session['user'])
        if not allowed:
            return jsonify([])
        q = supabase.table('inventory_alerts').select('*').eq('is_resolved', False).in_('venture_id', list(allowed))
        res = q.execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching alerts: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/inventory/resolve-alert/<alert_id>', methods=['POST'])
@requires_role_or_override('supervisor')
def api_inventory_resolve_alert(alert_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    try:
        supabase.table('inventory_alerts').update({'is_resolved': True}).eq('id', alert_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error resolving alert: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# User Management — New Routes
# ========================

@app.route('/api/users/create', methods=['POST'])
@requires_role('admin')
def api_users_create():
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    body = request.get_json() or {}
    email = (body.get('email') or '').strip()
    password = body.get('password', '')
    full_name = body.get('full_name', '')
    role = body.get('role', '')
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    if role not in ('supervisor', 'manager'):
        return jsonify({'error': 'Role must be supervisor or manager'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    try:
        existing = supabase.table('users').select('id').ilike('email', email).execute()
        if existing.data:
            return jsonify({'error': 'User with this email already exists'}), 409
        import uuid as _uuid
        new_user = {
            'id': str(_uuid.uuid4()),
            'email': email,
            'password_hash': generate_password_hash(password),
            'role': role,
            'full_name': full_name,
            'active': True,
            'org_id': session['user'].get('org_id')
        }
        supabase.table('users').insert(new_user).execute()
        return jsonify({'success': True, 'id': new_user['id']})
    except Exception as e:
        logger.error(f'Error creating user: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<user_id>', methods=['PUT'])
@requires_role('admin')
def api_users_update(user_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    if not _verify_same_org(user_id):
        return jsonify({'error': 'Forbidden: user belongs to a different organization'}), 403
    body = request.get_json() or {}
    update_fields = {}
    if 'full_name' in body:
        update_fields['full_name'] = body['full_name']
    if 'role' in body:
        if body['role'] not in ('admin', 'manager', 'supervisor'):
            return jsonify({'error': 'Invalid role'}), 400
        update_fields['role'] = body['role']
    if 'active' in body:
        if body['active'] is False and str(user_id) == str(session['user'].get('id')):
            return jsonify({'error': 'Cannot deactivate your own account'}), 400
        update_fields['active'] = body['active']
    if not update_fields:
        return jsonify({'error': 'No fields to update'}), 400
    try:
        supabase.table('users').update(update_fields).eq('id', user_id).execute()
        if 'active' in update_fields:
            _active_cache.pop(user_id, None)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error updating user: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<user_id>', methods=['DELETE'])
@requires_role('admin')
def api_users_delete(user_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    if str(user_id) == str(session['user'].get('id')):
        return jsonify({'error': 'Cannot deactivate your own account'}), 400
    if not _verify_same_org(user_id):
        return jsonify({'error': 'Forbidden: user belongs to a different organization'}), 403
    try:
        supabase.table('users').update({'active': False}).eq('id', user_id).execute()
        _active_cache.pop(user_id, None)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error deactivating user: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<user_id>/ventures')
@requires_role('admin')
def api_users_ventures(user_id):
    if not supabase:
        return jsonify([])
    if not _verify_same_org(user_id):
        return jsonify({'error': 'Forbidden'}), 403
    try:
        res = supabase.table('user_ventures').select('venture_id').eq('user_id', user_id).execute()
        return jsonify([r['venture_id'] for r in (res.data or [])])
    except Exception as e:
        logger.error(f'Error fetching user ventures: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<user_id>/ventures', methods=['POST'])
@requires_role('admin')
def api_users_ventures_set(user_id):
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    if not _verify_same_org(user_id):
        return jsonify({'error': 'Forbidden'}), 403
    body = request.get_json() or {}
    venture_ids = body.get('venture_ids', [])
    allowed = _allowed_ventures(session['user'])
    for vid in venture_ids:
        if vid not in allowed:
            return jsonify({'error': f'Venture {vid} is not in your organization'}), 403
    try:
        supabase.table('user_ventures').delete().eq('user_id', user_id).execute()
        if venture_ids:
            rows = [{'user_id': user_id, 'venture_id': vid} for vid in venture_ids]
            supabase.table('user_ventures').insert(rows).execute()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f'Error setting user ventures: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/ventures/with-names')
@requires_role('admin')
def api_ventures_with_names():
    if not supabase:
        return jsonify([])
    try:
        org_id = session['user'].get('org_id')
        res = supabase.table('ventures').select('id, name').eq('org_id', org_id).execute()
        return jsonify(res.data or [])
    except Exception as e:
        logger.error(f'Error fetching ventures with names: {e}')
        return jsonify({'error': str(e)}), 500


# ========================
# Venture-wise Analysis API
# ========================

@app.route('/api/venture-analysis')
@requires_role('supervisor', 'manager', 'admin')
def api_venture_analysis():
    """Aggregate work done/pending from cell_data and inventory usage from stock_ledger per venture."""
    if not supabase:
        return jsonify({'error': 'Supabase not connected'}), 500
    user = session.get('user') or {}
    org_id = user.get('org_id')
    allowed_ids = _allowed_ventures(user)

    # Fetch ventures
    venture_names = {}
    venture_structures = {}
    try:
        vres = supabase.table('ventures').select('*').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
        for v in (vres.data or []):
            if v.get('id') in allowed_ids and v.get('id') != '__all__':
                d = v.get('data') or {}
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        d = {}
                venture_names[v['id']] = (d or {}).get('name') or v.get('name') or v['id']
                venture_structures[v['id']] = d
    except Exception as e:
        logger.error(f'Error fetching ventures for venture analysis: {e}')

    # Fetch all cell_data and compute work done/pending per venture
    venture_work = {}
    try:
        cells_res = supabase.table('cell_data').select('*').execute()
        for row in (cells_res.data or []):
            cell_id = row.get('id', '')
            d = row.get('data') or {}
            color = d.get('color', '') or 'none'
            # Determine which venture this cell belongs to
            vid = None
            for v_id in allowed_ids:
                if cell_id.startswith(v_id + '_'):
                    vid = v_id
                    break
            if not vid:
                continue
            if vid not in venture_work:
                venture_work[vid] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
            venture_work[vid]['total'] += 1
            venture_work[vid][color] = (venture_work[vid].get(color, 0) or 0) + 1
    except Exception as e:
        logger.error(f'Error fetching cell_data for venture analysis: {e}')

    # Compute total possible cells from venture structure (untouched = red/pending)
    for vid, vdata in venture_structures.items():
        blocks = vdata.get('blocks', [])
        work_categories = vdata.get('work_categories', {})
        flat_view_items = vdata.get('flat_view_items', [])
        super_structure_items = vdata.get('super_structure_items', [])
        num_work_items = sum(len(items) if isinstance(items, list) else 0 for items in work_categories.values())
        num_flat_items = len(flat_view_items) if isinstance(flat_view_items, list) else 0
        num_ss_items = len(super_structure_items) if isinstance(super_structure_items, list) else 0
        total_items = num_work_items + num_flat_items + num_ss_items
        if total_items == 0:
            total_items = len(DEFAULT_WORK_ITEMS)
        total_possible = 0
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            floors = blk.get('floors', 5)
            flats_per_floor = blk.get('flats_per_floor', FLATS_PER_FLOOR)
            total_possible += floors * flats_per_floor * total_items
        if vid not in venture_work:
            venture_work[vid] = {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0}
        existing_total = venture_work[vid]['total']
        if total_possible > existing_total:
            untouched = total_possible - existing_total
            venture_work[vid]['total'] = total_possible
            venture_work[vid]['none'] = venture_work[vid].get('none', 0) + untouched

    # Fetch stock_ledger entries and group by purpose_venture_id
    # First compute average purchase rate per material from IN entries
    material_avg_rate = {}
    venture_inventory = {}
    try:
        all_entries = []
        offset = 0
        page_size = 1000
        while True:
            page = supabase.table('stock_ledger').select('*').order('created_at', desc=True).range(offset, offset + page_size - 1).execute()
            rows = page.data or []
            all_entries.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
        logger.info(f'Venture analysis: fetched {len(all_entries)} entries from stock_ledger')

        # First pass: compute average rate per material from IN entries
        material_in_totals = {}
        for entry in all_entries:
            if entry.get('entry_type') != 'IN' or entry.get('is_deleted'):
                continue
            mid = entry.get('material_id', 'unknown')
            qty = float(entry.get('qty') or 0)
            rate = float(entry.get('rate') or 0)
            if qty > 0:
                if mid not in material_in_totals:
                    material_in_totals[mid] = {'total_qty': 0.0, 'total_cost': 0.0}
                material_in_totals[mid]['total_qty'] += qty
                material_in_totals[mid]['total_cost'] += qty * rate
        for mid, t in material_in_totals.items():
            if t['total_qty'] > 0:
                material_avg_rate[mid] = t['total_cost'] / t['total_qty']

        # Second pass: process OUT entries for venture inventory
        for entry in all_entries:
            if entry.get('entry_type') != 'OUT' or entry.get('is_deleted'):
                continue
            pvid = entry.get('purpose_venture_id') or entry.get('venture_id')
            if not pvid:
                continue
            if pvid not in venture_inventory:
                venture_inventory[pvid] = {'total_qty': 0.0, 'total_cost': 0.0, 'materials': {}, 'by_flat': {}}
            qty = float(entry.get('qty') or 0)
            mid = entry.get('material_id', 'unknown')
            rate = float(entry.get('rate') or 0)
            if rate <= 0:
                rate = material_avg_rate.get(mid, 0)
            cost = qty * rate
            venture_inventory[pvid]['total_qty'] += qty
            venture_inventory[pvid]['total_cost'] += cost
            if mid not in venture_inventory[pvid]['materials']:
                venture_inventory[pvid]['materials'][mid] = {'qty': 0.0, 'cost': 0.0}
            venture_inventory[pvid]['materials'][mid]['qty'] += qty
            venture_inventory[pvid]['materials'][mid]['cost'] += cost
            # Track by flat if present
            flat = entry.get('flat')
            if flat:
                flat_key = str(flat)
                if flat_key not in venture_inventory[pvid]['by_flat']:
                    venture_inventory[pvid]['by_flat'][flat_key] = {'qty': 0.0, 'cost': 0.0, 'materials': {}}
                venture_inventory[pvid]['by_flat'][flat_key]['qty'] += qty
                venture_inventory[pvid]['by_flat'][flat_key]['cost'] += cost
                if mid not in venture_inventory[pvid]['by_flat'][flat_key]['materials']:
                    venture_inventory[pvid]['by_flat'][flat_key]['materials'][mid] = 0.0
                venture_inventory[pvid]['by_flat'][flat_key]['materials'][mid] += qty
        logger.info(f'Venture analysis: venture_inventory keys = {list(venture_inventory.keys())}')
        logger.info(f'Venture analysis: allowed_ids = {sorted(allowed_ids)}')
    except Exception as e:
        logger.error(f'Error fetching stock_ledger for venture analysis: {e}')

    # Fetch materials for names
    materials_map = {}
    try:
        mat_res = supabase.table('materials').select('id,name,unit,category').execute()
        for m in (mat_res.data or []):
            materials_map[m['id']] = {'name': m.get('name', m['id']), 'unit': m.get('unit', ''), 'category': m.get('category', '')}
    except Exception as e:
        logger.error(f'Error fetching materials for venture analysis: {e}')

    # Build response
    ventures = []
    for vid in sorted(allowed_ids):
        if vid == '__all__' or vid == 'WAREHOUSE':
            continue
        w = venture_work.get(vid, {'total': 0, 'green': 0, 'yellow': 0, 'blue': 0, 'red': 0, 'none': 0})
        inv = venture_inventory.get(vid, {'total_qty': 0, 'total_cost': 0, 'materials': {}, 'by_flat': {}})
        done = w.get('green', 0)
        total = w.get('total', 0)
        pending = total - done
        pct = round((done / total) * 100, 1) if total > 0 else 0

        # Top materials used
        top_materials = []
        for mid, mdata in sorted(inv['materials'].items(), key=lambda x: -x[1]['qty'])[:10]:
            mat_info = materials_map.get(mid, {'name': mid, 'unit': '', 'category': ''})
            top_materials.append({
                'name': mat_info['name'],
                'unit': mat_info.get('unit', ''),
                'category': mat_info.get('category', ''),
                'qty': round(mdata['qty'], 2),
                'cost': round(mdata['cost'], 2),
            })

        # Flat-wise usage
        flat_usage = []
        for flat_key, fdata in sorted(inv['by_flat'].items()):
            flat_materials = []
            for mid, mqty in sorted(fdata.get('materials', {}).items(), key=lambda x: -x[1])[:5]:
                mat_info = materials_map.get(mid, {'name': mid, 'unit': '', 'category': ''})
                flat_materials.append({'name': mat_info['name'], 'qty': round(mqty, 2)})
            flat_usage.append({
                'flat': flat_key,
                'qty': round(fdata['qty'], 2),
                'cost': round(fdata['cost'], 2),
                'materials': flat_materials,
            })

        ventures.append({
            'venture_id': vid,
            'venture_name': venture_names.get(vid, vid),
            'work_done': done,
            'work_pending': pending,
            'work_total': total,
            'work_pct': pct,
            'status_breakdown': {
                'green': w.get('green', 0),
                'yellow': w.get('yellow', 0),
                'blue': w.get('blue', 0),
                'red': w.get('red', 0) + w.get('none', 0),
            },
            'inventory_used_qty': round(inv['total_qty'], 2),
            'inventory_used_cost': round(inv['total_cost'], 2),
            'top_materials': top_materials,
            'flat_usage': flat_usage,
        })

    # Include WAREHOUSE (Central Warehouse) as a special entry for unassigned/legacy usage
    wh_inv = venture_inventory.get('WAREHOUSE', None)
    if wh_inv and wh_inv['total_qty'] > 0:
        wh_top = []
        for mid, mdata in sorted(wh_inv['materials'].items(), key=lambda x: -x[1]['qty'])[:10]:
            mat_info = materials_map.get(mid, {'name': mid, 'unit': '', 'category': ''})
            wh_top.append({
                'name': mat_info['name'],
                'unit': mat_info.get('unit', ''),
                'category': mat_info.get('category', ''),
                'qty': round(mdata['qty'], 2),
                'cost': round(mdata['cost'], 2),
            })
        wh_flat = []
        for flat_key, fdata in sorted(wh_inv['by_flat'].items()):
            wh_flat.append({
                'flat': flat_key,
                'qty': round(fdata['qty'], 2),
                'cost': round(fdata['cost'], 2),
                'materials': [],
            })
        ventures.append({
            'venture_id': 'WAREHOUSE',
            'venture_name': 'Central Warehouse (Unassigned Usage)',
            'work_done': 0,
            'work_pending': 0,
            'work_total': 0,
            'work_pct': 0,
            'status_breakdown': {'green': 0, 'yellow': 0, 'blue': 0, 'red': 0},
            'inventory_used_qty': round(wh_inv['total_qty'], 2),
            'inventory_used_cost': round(wh_inv['total_cost'], 2),
            'top_materials': wh_top,
            'flat_usage': wh_flat,
        })

    return jsonify({'ventures': ventures})


# ========================
# Daily Business Report API
# ========================

def _daily_report_ordinal(n):
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{'st' if n % 10 == 1 else 'nd' if n % 10 == 2 else 'rd' if n % 10 == 3 else 'th'}"


def _daily_report_format_date(dt):
    return f"{_daily_report_ordinal(dt.day)} {dt.strftime('%B %Y')} | {dt.strftime('%A')}"


def _fallback_daily_report(date_str):
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        dt = datetime.now()
    return {
        'report_date': _daily_report_format_date(dt),
        'raw_date': date_str,
        'company_name': 'VGrand Infra Pvt. Ltd.',
        'total_expenditure': 0,
        'work_done': 0,
        'pending_works': 0,
        'outstanding_amount': 0,
        'day_book': {
            'opening_balance': 0,
            'total_receipts': 0,
            'total_payments': 0,
            'closing_balance': 0,
        },
        'venture_analysis': [],
        'work_done_by_venture': [],
        'materials_purchases': [],
        'outstanding_by_party': [],
    }


@app.route('/api/daily-report')
@requires_role('manager', 'admin')
def api_daily_report():
    """Return live daily business report aggregates for the current org."""
    date_param = request.args.get('date')
    if date_param:
        try:
            report_dt = datetime.strptime(date_param, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format, use YYYY-MM-DD'}), 400
    else:
        report_dt = datetime.now(IST)
    date_str = report_dt.strftime('%Y-%m-%d')

    if not supabase:
        return jsonify(_fallback_daily_report(date_str))

    user = session.get('user') or {}
    org_id = user.get('org_id')
    allowed_ids = _allowed_ventures(user)

    venture_names = {}
    try:
        vres = supabase.table('ventures').select('*').or_(f'org_id.eq.{org_id},org_id.is.null').execute()
        for v in (vres.data or []):
            if v.get('id') in allowed_ids and v.get('id') != '__all__':
                d = v.get('data') or {}
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        d = {}
                venture_names[v['id']] = (d or {}).get('name') or v.get('name') or v['id']
    except Exception as e:
        logger.error(f'Error fetching ventures for daily report: {e}')

    vendors_map = {}
    try:
        ven_res = supabase.table('vendors').select('*').execute()
        for v in (ven_res.data or []):
            d = v.get('data') or {}
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except Exception:
                    d = {}
            vendors_map[v['id']] = d.get('name') or v.get('name') or v['id']
    except Exception as e:
        logger.error(f'Error fetching vendors for daily report: {e}')

    expenditures = []
    try:
        expenditures = supabase.table('expenditures').select('*').execute().data or []
    except Exception as e:
        logger.error(f'Error fetching expenditures for daily report: {e}')

    invoices = []
    try:
        invoices = supabase.table('invoices').select('*').execute().data or []
    except Exception as e:
        logger.error(f'Error fetching invoices for daily report: {e}')

    cell_rows = []
    try:
        cell_rows = supabase.table('cell_data').select('*').execute().data or []
    except Exception as e:
        logger.error(f'Error fetching cell_data for daily report: {e}')

    contracts = []
    contract_payments = []
    try:
        contracts = supabase.table('contractor_contracts').select('*').eq('org_id', org_id).execute().data or []
        contract_ids = [c['id'] for c in contracts if c.get('status') != 'cancelled']
        if contract_ids:
            contract_payments = supabase.table('contractor_payments').select('*').in_('contract_id', contract_ids).execute().data or []
    except Exception as e:
        logger.error(f'Error fetching contractor data for daily report: {e}')

    inv_purchases = []
    inv_payments = []
    try:
        inv_purchases = supabase.table('inventory_purchases').select('*').eq('org_id', org_id).execute().data or []
        inv_purchase_ids = [p['id'] for p in inv_purchases]
        if inv_purchase_ids:
            inv_payments = supabase.table('inventory_purchase_payments').select('*').in_('purchase_id', inv_purchase_ids).execute().data or []
    except Exception as e:
        logger.error(f'Error fetching inventory data for daily report: {e}')

    today_expenditure = 0.0
    venture_expenditure_today = defaultdict(float)
    for r in expenditures:
        d = r.get('data') or {}
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                d = {}
        if r.get('venture_id') in allowed_ids and d.get('date') == date_str:
            amt = float(d.get('amount', 0) or 0)
            today_expenditure += amt
            venture_expenditure_today[r.get('venture_id')] += amt

    contract_payment_totals = defaultdict(float)
    today_contractor_payments = 0.0
    for p in contract_payments:
        if p.get('is_deleted'):
            continue
        amt = float(p.get('amount', 0) or 0)
        contract_payment_totals[p.get('contract_id')] += amt
        if p.get('payment_date') == date_str:
            today_contractor_payments += amt

    inv_payment_totals = defaultdict(float)
    today_inventory_payments = 0.0
    for p in inv_payments:
        amt = float(p.get('amount', 0) or 0)
        inv_payment_totals[p.get('purchase_id')] += amt
        if p.get('payment_date') == date_str:
            today_inventory_payments += amt

    total_expenditure = today_expenditure + today_contractor_payments + today_inventory_payments

    total_receipts = 0.0
    venture_receipts = defaultdict(float)
    for r in invoices:
        d = r.get('data') or {}
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                d = {}
        vid = d.get('ventureId')
        if vid in allowed_ids and d.get('purchaseDate') == date_str:
            amt = float(d.get('amount', 0) or 0)
            total_receipts += amt
            venture_receipts[vid] += amt

    opening_balance = 0.0
    try:
        ob_res = supabase.table('settings').select('*').eq('key', 'daily_report_opening_balance').execute()
        if ob_res.data:
            val = ob_res.data[0].get('value')
            if isinstance(val, dict):
                opening_balance = float(val.get(date_str, val.get('default', 0)) or 0)
            else:
                opening_balance = float(val or 0)
    except Exception as e:
        logger.error(f'Error fetching opening balance for daily report: {e}')

    closing_balance = opening_balance + total_receipts - total_expenditure

    # Contract value baseline per venture
    venture_contract_value = defaultdict(float)
    for c in contracts:
        if c.get('status') == 'cancelled':
            continue
        vid = c.get('venture_id') or '_unknown'
        if vid in allowed_ids:
            venture_contract_value[vid] += float(c.get('total_amount', 0) or 0)

    # Cell-level progress per venture
    cell_stats = defaultdict(lambda: {'total': 0, 'green': 0, 'blue': 0, 'yellow': 0, 'red': 0, 'none': 0})
    for row in cell_rows:
        vid = row['id'].split('_')[0] if '_' in row['id'] else '_unknown'
        if vid not in allowed_ids:
            continue
        d = row.get('data') or {}
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                d = {}
        color = d.get('color') or 'none'
        cell_stats[vid]['total'] += 1
        cell_stats[vid][color] += 1

    # Work done / pending derived from actual cell color progress scaled by contract value
    work_done = 0.0
    pending_works = 0.0
    venture_work_done = defaultdict(float)
    for vid, stats in cell_stats.items():
        if vid not in allowed_ids:
            continue
        total_cells = stats['total']
        if total_cells == 0:
            continue
        weighted = (stats['green'] * 100 + stats['blue'] * 75 + stats['yellow'] * 40) / total_cells
        contract_total = venture_contract_value.get(vid, 0.0)
        if contract_total > 0:
            completed_value = contract_total * weighted / 100
            pending_value = contract_total - completed_value
        else:
            completed_value = 0.0
            pending_value = 0.0
        work_done += completed_value
        pending_works += pending_value
        venture_work_done[vid] += completed_value

    # Ventures with contracts but no cell data count as fully pending
    for vid, contract_total in venture_contract_value.items():
        if vid in allowed_ids and vid not in cell_stats:
            pending_works += contract_total

    # Ensure all allowed ventures appear in the analysis tables
    for vid in allowed_ids:
        if vid in ('__all__', 'WAREHOUSE'):
            continue
        if vid not in venture_contract_value:
            venture_contract_value[vid] = 0.0
        if vid not in venture_work_done:
            venture_work_done[vid] = 0.0

    party_outstanding_raw = defaultdict(float)
    party_display = {}
    for c in contracts:
        if c.get('status') == 'cancelled':
            continue
        total = float(c.get('total_amount', 0) or 0)
        paid = contract_payment_totals.get(c['id'], 0.0)
        name = (c.get('person_name') or 'Unknown Contractor').strip()
        key = name.lower()
        party_outstanding_raw[key] += total - paid
        party_display.setdefault(key, name)

    for p in inv_purchases:
        amt = float(p.get('amount', 0) or 0)
        paid = inv_payment_totals.get(p['id'], 0.0)
        name = (p.get('vendor_name') or vendors_map.get(p.get('vendor_id')) or 'Unknown Vendor').strip()
        key = name.lower()
        party_outstanding_raw[key] += amt - paid
        party_display.setdefault(key, name)

    party_outstanding = {party_display[k]: v for k, v in party_outstanding_raw.items()}
    total_outstanding = sum(party_outstanding.values())

    venture_analysis = []
    total_turnover = sum(venture_contract_value.values())
    if total_turnover > 0:
        for vid, amount in sorted(venture_contract_value.items(), key=lambda x: -x[1]):
            venture_analysis.append({
                'name': venture_names.get(vid, 'Unknown'),
                'amount': round(amount, 2),
                'pct': round((amount / total_turnover) * 100, 1) if total_turnover else 0,
            })
    else:
        for vid in set(venture_receipts.keys()) | set(venture_expenditure_today.keys()):
            venture_analysis.append({
                'name': venture_names.get(vid, 'Unknown'),
                'amount': round(venture_receipts.get(vid, 0) + venture_expenditure_today.get(vid, 0), 2),
                'pct': 0,
            })
        total_turnover = sum(v['amount'] for v in venture_analysis)
        if total_turnover > 0:
            for v in venture_analysis:
                v['pct'] = round((v['amount'] / total_turnover) * 100, 1)

    work_done_by_venture = []
    if work_done > 0:
        for vid, amount in sorted(venture_work_done.items(), key=lambda x: -x[1]):
            work_done_by_venture.append({
                'name': venture_names.get(vid, 'Unknown'),
                'amount': round(amount, 2),
                'pct': round((amount / work_done) * 100, 1) if work_done else 0,
            })

    materials = defaultdict(lambda: {'qty': 0.0, 'amount': 0.0, 'unit': ''})
    total_material_purchase = 0.0
    for p in inv_purchases:
        if p.get('invoice_date') == date_str:
            m = p.get('material_name') or 'Others'
            materials[m]['qty'] += float(p.get('qty', 0) or 0)
            materials[m]['amount'] += float(p.get('amount', 0) or 0)
            if not materials[m]['unit'] and p.get('unit'):
                materials[m]['unit'] = p.get('unit')
            total_material_purchase += float(p.get('amount', 0) or 0)

    materials_purchases = [
        {'name': k, 'qty': round(v['qty'], 2), 'unit': v['unit'], 'amount': round(v['amount'], 2)}
        for k, v in sorted(materials.items(), key=lambda x: -x[1]['amount'])
    ]

    outstanding_by_party = []
    if total_outstanding > 0:
        for name, amount in sorted(party_outstanding.items(), key=lambda x: -x[1]):
            outstanding_by_party.append({
                'name': name,
                'amount': round(amount, 2),
                'pct': round((amount / total_outstanding) * 100, 1) if total_outstanding else 0,
            })

    company_name = 'VGrand Infra Pvt. Ltd.'
    try:
        cn = supabase.table('settings').select('*').eq('key', 'company_name').execute()
        if cn.data:
            val = cn.data[0].get('value')
            if isinstance(val, dict):
                company_name = val.get('name') or company_name
            elif val:
                company_name = str(val)
    except Exception:
        pass

    return jsonify({
        'report_date': _daily_report_format_date(report_dt),
        'raw_date': date_str,
        'company_name': company_name,
        'total_expenditure': round(total_expenditure, 2),
        'work_done': round(work_done, 2),
        'pending_works': round(pending_works, 2),
        'outstanding_amount': round(total_outstanding, 2),
        'day_book': {
            'opening_balance': round(opening_balance, 2),
            'total_receipts': round(total_receipts, 2),
            'total_payments': round(total_expenditure, 2),
            'closing_balance': round(closing_balance, 2),
        },
        'venture_analysis': venture_analysis,
        'work_done_by_venture': work_done_by_venture,
        'materials_purchases': materials_purchases,
        'outstanding_by_party': outstanding_by_party,
    })


if __name__ == '__main__':
    _dev = os.environ.get('DEV_MODE') == '1' or os.environ.get('FLASK_ENV') == 'development'
    if _dev:
        logger.info('Starting in DEVELOPMENT mode with debug=True')
    app.run(debug=_dev, host='0.0.0.0', port=5000, use_reloader=False)
