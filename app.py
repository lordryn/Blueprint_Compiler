import os
import json
import re
import logging
from logging.handlers import RotatingFileHandler
from sqlalchemy import event
from flask import Flask, render_template, request, redirect, url_for, session, abort, flash, jsonify
from flask_wtf.csrf import CSRFProtect
from flask_apscheduler import APScheduler
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Organization, Role, UserOrganizationRole, Claim, Requisition, JoinRequest, MaterialInventory, CraftingJob, FinishedItem
from bp_catalog_grabber import pull_fabricator_blueprints
from blueprint_parser import process_blueprints
import datetime
from datetime import timedelta

app = Flask(__name__)
# Use a static fallback for development, but in production this should be set via env
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_default_secret_key_change_in_production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///crafters.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

csrf = CSRFProtect(app)
db.init_app(app)
# Logging setup
if not os.environ.get('TESTING'):
    file_handler = RotatingFileHandler('app.log', maxBytes=1024 * 1024 * 10, backupCount=5)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Blueprint Compiler startup')

from flask import has_request_context

@event.listens_for(db.session, "after_flush")
def receive_after_flush(session_db, flush_context):
    if os.environ.get('TESTING'):
        return
        
    actor = "System"
    if has_request_context() and 'username' in session:
        actor = f"User:{session['username']}"
        if request.view_args and 'org_slug' in request.view_args:
            actor += f" Org:{request.view_args['org_slug']}"
            
    for obj in session_db.new:
        app.logger.info(f"[{actor}] DB INSERT: {repr(obj)}")
    for obj in session_db.dirty:
        app.logger.info(f"[{actor}] DB UPDATE: {repr(obj)}")
    for obj in session_db.deleted:
        app.logger.info(f"[{actor}] DB DELETE: {repr(obj)}")

# Scheduler setup
scheduler = APScheduler()
scheduler.api_enabled = True
scheduler.init_app(app)
if not os.environ.get('TESTING'):
    scheduler.start()

@scheduler.task('cron', id='update_catalog', hour='0', minute='0')
def scheduled_catalog_update():
    # Since background tasks don't have request context, we just run the pipeline functions
    try:
        pull_fabricator_blueprints()
        process_blueprints('blueprints unprocessed.txt', 'blueprints.json')
        app.logger.info("Scheduled catalog update completed successfully")
    except Exception as e:
        app.logger.error(f"Scheduled update failed: {e}")

def parse_crafting_time(time_str):
    if not time_str:
        return timedelta()
    
    time_str = str(time_str).lower().strip()
    total_seconds = 0
    
    matches = re.finditer(r'([\d.]+)\s*([hms])', time_str)
    found_any = False
    for match in matches:
        found_any = True
        val = float(match.group(1))
        unit = match.group(2)
        if unit == 'h':
            total_seconds += val * 3600
        elif unit == 'm':
            total_seconds += val * 60
        elif unit == 's':
            total_seconds += val
            
    if not found_any:
        try:
            total_seconds = float(time_str)
        except ValueError:
            pass
            
    return timedelta(seconds=total_seconds)

@scheduler.task('interval', id='process_crafting_jobs', seconds=10)
def process_crafting_jobs():
    with app.app_context():
        now = datetime.datetime.utcnow()
        jobs = CraftingJob.query.filter(CraftingJob.status == 'In Progress', CraftingJob.completion_time <= now).all()
        for job in jobs:
            job.status = 'Completed'
            
            existing = None
            if not job.requisition_id:
                existing = FinishedItem.query.filter_by(
                    organization_id=job.organization_id,
                    blueprint_name=job.blueprint_name,
                    requisition_id=None
                ).first()
                
            if existing:
                existing.quantity += 1
            else:
                fi = FinishedItem(organization_id=job.organization_id, blueprint_name=job.blueprint_name, requisition_id=job.requisition_id, notes=job.notes)
                db.session.add(fi)
        
        if jobs:
            db.session.commit()
            app.logger.info(f"Processed {len(jobs)} completed crafting jobs")

# Database initialization wrapper
if not os.environ.get('TESTING'):
    with app.app_context():
        db.create_all()

        # Initialize basic Roles if empty
        if Role.query.count() == 0:
            admin_role = Role(name='Admin', description='Full control over membership, roles, and grabber')
            manager_role = Role(name='Manager', description='Control claims and requisitions')
            member_role = Role(name='Member', description='Standard user, can make claims and view lists')
            viewer_role = Role(name='Viewer', description='Read-only observer')
            db.session.add_all([admin_role, manager_role, member_role, viewer_role])
            db.session.commit()


# Helper function for password hashing
def hash_password(password):
    return generate_password_hash(password)


def check_password(stored_hash, password):
    try:
        return check_password_hash(stored_hash, password)
    except Exception:
        return False

@app.before_request
def check_valid_session():
    if 'user_id' in session:
        # If the user's session exists but the user is not in the database (e.g. DB wiped)
        user = User.query.get(session['user_id'])
        if not user:
            session.clear()
            flash("Your session has expired or your account was deleted. Please register or log in again.", "error")
            return redirect(url_for('login'))


# Custom Authentication Decorators
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def organization_role_required(allowed_roles):
    """
    Decorator to restrict access based on organization roles.
    Expects 'org_slug' as a route parameter.
    """
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            org_slug = kwargs.get('org_slug')
            if not org_slug:
                abort(400, description="Organization slug missing.")

            org = Organization.query.filter_by(slug=org_slug).first_or_404()
            user_id = session.get('user_id')
            if not user_id:
                return redirect(url_for('login'))

            user_role = UserOrganizationRole.query.filter_by(
                user_id=user_id,
                organization_id=org.id
            ).first()

            if not user_role or user_role.role.name not in allowed_roles:
                abort(403, description="You do not have permission to access this organization's page.")

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def site_admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or not user.is_site_admin:
            abort(403, description="You must be a Site Administrator to access this page.")
        return f(*args, **kwargs)
    return decorated_function


# Master Blueprint data loader with simple caching
_blueprint_catalog_cache = []
_blueprint_catalog_mtime = 0

def load_blueprint_catalog():
    global _blueprint_catalog_cache, _blueprint_catalog_mtime
    catalog_path = 'blueprints.json'
    if not os.path.exists(catalog_path):
        default_catalog = [
            {
                "member_name": "Org Armory",
                "blueprint_name": "Metamaterial Test #146",
                "category": "Other",
                "manufacturer": "",
                "grade": "",
                "size": "1",
                "materials": [
                    {
                        "slot": "Substrate",
                        "name": "Titanium",
                        "amount": "2 SCU",
                        "formatted": "Substrate: Titanium (2 SCU)"
                    }
                ],
                "crafting_time": "70s"
            }
        ]
        with open(catalog_path, 'w', encoding='utf-8') as f:
            json.dump(default_catalog, f, indent=4)
        return default_catalog

    try:
        current_mtime = os.path.getmtime(catalog_path)
        if current_mtime != _blueprint_catalog_mtime or not _blueprint_catalog_cache:
            with open(catalog_path, 'r', encoding='utf-8') as f:
                _blueprint_catalog_cache = json.load(f)
            _blueprint_catalog_mtime = current_mtime
        return _blueprint_catalog_cache
    except Exception:
        return _blueprint_catalog_cache or []


# --- Standard Core Routes ---

@app.route('/')
def index():
    if 'user_id' in session:
        user_roles = UserOrganizationRole.query.filter_by(user_id=session['user_id']).all()
        return render_template('portal.html', user_roles=user_roles)
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username_or_email = request.form.get('username_or_email')
        password = request.form.get('password')

        user = User.query.filter((User.username == username_or_email) | (User.email == username_or_email)).first()
        if user and check_password(user.password_hash, password):
            if user.status == 'Pending':
                flash("Your account is pending administrator approval.", "warning")
                return redirect(url_for('login'))
            elif user.status == 'Suspended':
                flash("Your account has been suspended.", "error")
                return redirect(url_for('login'))
                
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_site_admin'] = user.is_site_admin
            flash("Welcome back!", "success")
            return redirect(url_for('index'))

        flash("Invalid login credentials.", "error")
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        email = request.form.get('email').strip()
        password = request.form.get('password')

        if not username or not email or not password:
            flash("All fields are required.", "error")
            return redirect(url_for('register'))

        if not re.match(r'^[\w.@+-]+$', username):
            flash("Username contains invalid characters.", "error")
            return redirect(url_for('register'))

        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            flash("Username or email already exists.", "error")
            return redirect(url_for('register'))

        pw_hash = hash_password(password)
        
        is_first_user = User.query.count() == 0
        status = 'Approved' if is_first_user else 'Pending'
        is_site_admin = True if is_first_user else False

        new_user = User(username=username, email=email, password_hash=pw_hash, is_site_admin=is_site_admin, status=status)
        db.session.add(new_user)
        db.session.commit()

        if status == 'Approved':
            session['user_id'] = new_user.id
            session['username'] = new_user.username
            session['is_site_admin'] = new_user.is_site_admin
            flash("Account created successfully! You are the Site Admin.", "success")
            return redirect(url_for('index'))
        else:
            flash("Account created! Your account is pending administrator approval.", "info")
            return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    flash("Successfully logged out.", "success")
    return redirect(url_for('login'))


@app.route('/org/create', methods=['GET', 'POST'])
@login_required
def create_organization():
    if request.method == 'POST':
        name = request.form.get('name').strip()
        slug = request.form.get('slug').strip().lower().replace(" ", "-")

        if not name or not slug:
            flash("All fields are required.", "error")
            return redirect(url_for('create_organization'))

        if not re.match(r'^[a-z0-9-]+$', slug):
            flash("Organization URL slug can only contain lowercase letters, numbers, and hyphens.", "error")
            return redirect(url_for('create_organization'))

        existing_org = Organization.query.filter_by(slug=slug).first()
        if existing_org:
            flash("An organization with that URL slug already exists.", "error")
            return redirect(url_for('create_organization'))

        new_org = Organization(name=name, slug=slug)
        db.session.add(new_org)
        db.session.commit()

        # Creator is automatically the Admin
        admin_role = Role.query.filter_by(name='Admin').first()
        membership = UserOrganizationRole(user_id=session['user_id'], organization_id=new_org.id, role_id=admin_role.id)
        db.session.add(membership)
        db.session.commit()

        flash(f"Organization '{name}' registered successfully!", "success")
        return redirect(url_for('index'))
    return render_template('create_org.html')


# --- Tenant-Specific Requisition & Claims Routes ---

@app.route('/org/<org_slug>')
@login_required
def organization_dashboard(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    user_id = session.get('user_id')

    # Check if user has membership in this organization
    user_role = UserOrganizationRole.query.filter_by(
        user_id=user_id,
        organization_id=org.id
    ).first()

    if not user_role:
        # Check if they have a pending join request
        join_req = JoinRequest.query.filter_by(
            user_id=user_id,
            organization_id=org.id
        ).first()

        if join_req:
            return render_template('join_pending.html', org=org, join_req=join_req)
        else:
            return redirect(url_for('join_org', org_slug=org_slug))

    # Load Master Catalog
    blueprints = load_blueprint_catalog()
    categories = sorted(list(set(bp.get('category', 'Unknown') for bp in blueprints)))

    # Fetch active claim list inside this organization
    org_claims = Claim.query.filter_by(organization_id=org.id).all()

    crafters_map = {}
    for c in org_claims:
        if c.blueprint_name not in crafters_map:
            crafters_map[c.blueprint_name] = []
            
        username = c.user.username if c.user else f"Unknown User ({c.user_id})"
        crafters_map[c.blueprint_name].append({
            'username': username,
            'user_id': c.user_id,
            'claim_id': c.id
        })

    # Requisitions
    org_reqs = Requisition.query.filter_by(organization_id=org.id, status='Pending').all()

    material_totals_dict = {}
    bp_dict = {bp['blueprint_name']: bp for bp in blueprints}

    for req in org_reqs:
        bp = bp_dict.get(req.blueprint_name)
        if bp and 'materials' in bp:
            for mat in bp['materials']:
                mat_name = mat.get('name')
                amt_str = mat.get('amount', '0')
                
                match = re.match(r"^([\d.]+)\s*(.*)$", str(amt_str).strip())
                if match:
                    val = float(match.group(1))
                    unit = match.group(2).strip()
                else:
                    val = 0.0
                    unit = ""
                
                total_val = val * req.quantity
                
                key = (mat_name, unit)
                if key not in material_totals_dict:
                    material_totals_dict[key] = 0.0
                material_totals_dict[key] += total_val
                
    inventory_records = MaterialInventory.query.filter_by(organization_id=org.id).all()
    total_inventory = {}
    inv_breakdown = {}
    for inv in inventory_records:
        total_inventory[inv.material_name] = inv.grade_baseline + inv.grade_improved + inv.grade_high_quality + inv.grade_exceptional
        inv_breakdown[inv.material_name] = {
            'baseline': inv.grade_baseline,
            'improved': inv.grade_improved,
            'high_quality': inv.grade_high_quality,
            'exceptional': inv.grade_exceptional
        }

    material_totals = []
    for (name, unit), total in material_totals_dict.items():
        total_str = f"{total:g}"
        amount_str = f"{total_str} {unit}".strip()
        
        avail_amt = total_inventory.get(name, 0.0)
        avail_str = f"{avail_amt:g} {unit}".strip() if avail_amt > 0 else f"0 {unit}".strip()
        breakdown = inv_breakdown.get(name, {'baseline': 0.0, 'improved': 0.0, 'high_quality': 0.0, 'exceptional': 0.0})
        
        material_totals.append({
            'name': name, 
            'amount': amount_str,
            'available': avail_str,
            'has_enough': avail_amt >= total,
            'breakdown': breakdown
        })
        
    material_totals.sort(key=lambda x: x['name'])

    return render_template('dashboard.html',
                           org=org,
                           blueprints=blueprints,
                           categories=categories,
                           crafters_map=crafters_map,
                           requisitions=org_reqs,
                           material_totals=material_totals,
                           user_role=user_role.role.name)


# --- Join Organization Request Portal ---

@app.route('/org/<org_slug>/join', methods=['GET', 'POST'])
@login_required
def join_org(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    user_id = session.get('user_id')

    # Verify they don't already have membership
    existing_membership = UserOrganizationRole.query.filter_by(
        user_id=user_id,
        organization_id=org.id
    ).first()

    if existing_membership:
        flash("You are already a member of this organization.", "info")
        return redirect(url_for('organization_dashboard', org_slug=org_slug))

    # Verify they don't already have a pending join request
    existing_request = JoinRequest.query.filter_by(
        user_id=user_id,
        organization_id=org.id
    ).first()

    if request.method == 'POST':
        if not existing_request:
            new_request = JoinRequest(
                user_id=user_id,
                organization_id=org.id,
                status='Pending'
            )
            db.session.add(new_request)
            db.session.commit()
            flash("Join request sent to the organization administrator.", "success")
        else:
            flash("You already have a pending request for this organization.", "info")
        return redirect(url_for('organization_dashboard', org_slug=org_slug))

    return render_template('join.html', org=org, existing_request=existing_request)


@app.route('/org/<org_slug>/claim', methods=['POST'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def claim_blueprint(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    blueprint_name = request.form.get('blueprint_name')

    existing_claim = Claim.query.filter_by(
        user_id=session['user_id'],
        organization_id=org.id,
        blueprint_name=blueprint_name
    ).first()

    if not existing_claim:
        new_claim = Claim(
            user_id=session['user_id'],
            organization_id=org.id,
            blueprint_name=blueprint_name
        )
        db.session.add(new_claim)
        db.session.commit()
        flash("Blueprint claim registered successfully.", "success")
    else:
        flash("You have already claimed this blueprint.", "info")

    return redirect(url_for('organization_dashboard', org_slug=org_slug))


@app.route('/org/<org_slug>/requisition', methods=['POST'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def submit_requisition(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    blueprint_name = request.form.get('blueprint_name')
    qty = int(request.form.get('quantity', 1))
    notes = request.form.get('notes', '').strip()

    new_req = Requisition(
        user_id=session['user_id'],
        organization_id=org.id,
        blueprint_name=blueprint_name,
        quantity=qty,
        notes=notes
    )
    db.session.add(new_req)
    db.session.commit()
    flash("Requisition submitted to organization crafters.", "success")
    return redirect(url_for('requisitions_page', org_slug=org_slug))

@app.route('/org/<org_slug>/requisitions', methods=['GET'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def requisitions_page(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    requisitions = Requisition.query.filter_by(organization_id=org.id).order_by(Requisition.status, Requisition.created_at.desc()).all()
    user_role = UserOrganizationRole.query.filter_by(user_id=session['user_id'], organization_id=org.id).first()
    blueprints = load_blueprint_catalog()
    return render_template('requisitions.html', org=org, requisitions=requisitions, user_role=user_role.role.name if user_role else 'Viewer', blueprints=blueprints)

@app.route('/org/<org_slug>/claim/<int:claim_id>/delete', methods=['POST'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def delete_claim(org_slug, claim_id):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    claim = Claim.query.filter_by(id=claim_id, organization_id=org.id).first_or_404()
    
    # Check permissions: User must own the claim OR be an Admin
    user_role = UserOrganizationRole.query.filter_by(user_id=session['user_id'], organization_id=org.id).first()
    if claim.user_id != session['user_id'] and (not user_role or user_role.role.name != 'Admin'):
        abort(403, description="You do not have permission to delete this claim.")
        
    db.session.delete(claim)
    db.session.commit()
    flash("Blueprint claim removed successfully.", "success")
    return redirect(url_for('organization_dashboard', org_slug=org_slug))


@app.route('/org/<org_slug>/requisition/<int:req_id>/delete', methods=['POST'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def delete_requisition(org_slug, req_id):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    req = Requisition.query.filter_by(id=req_id, organization_id=org.id).first_or_404()
    
    # Check permissions: User must own the requisition OR be an Admin
    user_role = UserOrganizationRole.query.filter_by(user_id=session['user_id'], organization_id=org.id).first()
    if req.user_id != session['user_id'] and (not user_role or user_role.role.name != 'Admin'):
        abort(403, description="You do not have permission to delete this requisition.")
        
    db.session.delete(req)
    db.session.commit()
    flash("Requisition removed successfully.", "success")
    return redirect(url_for('organization_dashboard', org_slug=org_slug))

@app.route('/org/<org_slug>/requisition/<int:req_id>/status', methods=['POST'])
@login_required
@organization_role_required(['Admin', 'Manager'])
def edit_requisition_status(org_slug, req_id):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    req = Requisition.query.filter_by(id=req_id, organization_id=org.id).first_or_404()
    
    new_status = request.form.get('status')
    if new_status in ['Pending', 'In Progress', 'Fulfilled', 'Cancelled']:
        req.status = new_status
        if new_status == 'Pending':
            req.crafter_id = None
        db.session.commit()
        flash(f"Requisition status updated to {new_status}.", "success")
        
    return redirect(url_for('requisitions_page', org_slug=org_slug))


# --- Material Logging & What Can Be Made ---

@app.route('/org/<org_slug>/materials', methods=['GET', 'POST'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def organization_materials(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    
    if request.method == 'POST':
        inv_map = {}
        for key, value in request.form.items():
            if key.startswith('mat_'):
                parts = key.split('_')
                if len(parts) >= 3:
                    grade = parts[-1]
                    material_name = '_'.join(parts[1:-1])
                    
                    try:
                        val = float(value)
                    except ValueError:
                        continue
                        
                    if material_name not in inv_map:
                        inv = MaterialInventory.query.filter_by(organization_id=org.id, material_name=material_name).first()
                        if not inv:
                            inv = MaterialInventory(organization_id=org.id, material_name=material_name)
                            db.session.add(inv)
                        inv_map[material_name] = inv
                    else:
                        inv = inv_map[material_name]
                        
                    import math
                    def floats_differ(f1, f2):
                        if f1 is None: f1 = 0.0
                        if f2 is None: f2 = 0.0
                        return not math.isclose(float(f1), float(f2), abs_tol=1e-5)

                    if grade == 'baseline' and floats_differ(inv.grade_baseline, val):
                        inv.grade_baseline = val
                    elif grade == 'improved' and floats_differ(inv.grade_improved, val):
                        inv.grade_improved = val
                    elif grade == 'highquality' and floats_differ(inv.grade_high_quality, val):
                        inv.grade_high_quality = val
                    elif grade == 'exceptional' and floats_differ(inv.grade_exceptional, val):
                        inv.grade_exceptional = val
                        
        db.session.commit()
        flash("Material inventory updated successfully.", "success")
        return redirect(url_for('organization_materials', org_slug=org_slug))

    inventory_records = MaterialInventory.query.filter_by(organization_id=org.id).all()
    blueprints = load_blueprint_catalog()
    
    total_inventory = {}
    for inv in inventory_records:
        total = inv.grade_baseline + inv.grade_improved + inv.grade_high_quality + inv.grade_exceptional
        total_inventory[inv.material_name] = total
        
    craftable_blueprints = []
    
    for bp in blueprints:
        if not bp.get('materials'):
            continue
            
        max_craftable = float('inf')
        for mat in bp['materials']:
            mat_name = mat.get('name')
            amt_str = mat.get('amount', '0')
            
            match = re.match(r"^([\d.]+)\s*(.*)$", str(amt_str).strip())
            if match:
                req_val = float(match.group(1))
            else:
                req_val = 0.0
                
            if req_val > 0:
                avail = total_inventory.get(mat_name, 0.0)
                craftable = int(avail // req_val)
                if craftable < max_craftable:
                    max_craftable = craftable
                    
        if max_craftable > 0 and max_craftable != float('inf'):
            craftable_blueprints.append({
                'blueprint_name': bp['blueprint_name'],
                'category': bp.get('category', 'Other'),
                'max_craftable': max_craftable,
                'materials': bp.get('materials', [])
            })
            
    craftable_blueprints.sort(key=lambda x: (-x['max_craftable'], x['blueprint_name']))

    known_materials = set()
    for bp in blueprints:
        if bp.get('materials'):
            for mat in bp['materials']:
                known_materials.add(mat.get('name'))
                
    material_list = []
    inv_dict = {inv.material_name: inv for inv in inventory_records}
    for m in sorted(known_materials):
        inv = inv_dict.get(m)
        material_list.append({
            'name': m,
            'baseline': inv.grade_baseline if inv else 0.0,
            'improved': inv.grade_improved if inv else 0.0,
            'high_quality': inv.grade_high_quality if inv else 0.0,
            'exceptional': inv.grade_exceptional if inv else 0.0,
            'total': (inv.grade_baseline + inv.grade_improved + inv.grade_high_quality + inv.grade_exceptional) if inv else 0.0
        })

    user_role = UserOrganizationRole.query.filter_by(user_id=session['user_id'], organization_id=org.id).first()

    return render_template('materials.html',
                           org=org,
                           material_list=material_list,
                           craftable_blueprints=craftable_blueprints,
                           all_blueprints=blueprints,
                           user_role=user_role.role.name if user_role else 'Viewer')

# --- Unified Admin Dashboard ---

@app.route('/org/<org_slug>/admin', methods=['GET', 'POST'])
@login_required
@organization_role_required(['Admin'])
def organization_admin(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()

    if request.method == 'POST':
        action_type = request.form.get('action_type')

        if action_type == 'update_role':
            target_user_id = request.form.get('user_id')
            new_role_id = request.form.get('role_id')

            membership = UserOrganizationRole.query.filter_by(
                user_id=target_user_id,
                organization_id=org.id
            ).first()

            if membership:
                # Prevent demoting the last organization administrator
                if int(target_user_id) == session['user_id'] and Role.query.get(new_role_id).name != 'Admin':
                    num_admins = UserOrganizationRole.query.filter_by(
                        organization_id=org.id,
                        role_id=Role.query.filter_by(name='Admin').first().id
                    ).count()
                    if num_admins <= 1:
                        flash("Action blocked: You are the sole administrator of this organization.", "error")
                        return redirect(url_for('organization_admin', org_slug=org_slug))

                membership.role_id = new_role_id
                db.session.commit()
                flash("User role modified successfully.", "success")

    memberships = UserOrganizationRole.query.filter_by(organization_id=org.id).all()
    all_roles = Role.query.all()

    # Load Pending Join Requests
    pending_requests = JoinRequest.query.filter_by(
        organization_id=org.id,
        status='Pending'
    ).all()

    return render_template('admin.html',
                           org=org,
                           memberships=memberships,
                           all_roles=all_roles,
                           pending_requests=pending_requests)


# --- Admin Actions: Join Request Approval & Rejection ---

@app.route('/org/<org_slug>/admin/approve-request/<int:request_id>', methods=['POST'])
@login_required
@organization_role_required(['Admin'])
def admin_approve_join_request(org_slug, request_id):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    join_req = JoinRequest.query.filter_by(id=request_id, organization_id=org.id).first_or_404()

    if join_req.status == 'Pending':
        join_req.status = 'Approved'

        # Verify the user doesn't already have membership
        existing_membership = UserOrganizationRole.query.filter_by(
            user_id=join_req.user_id,
            organization_id=org.id
        ).first()

        if not existing_membership:
            member_role = Role.query.filter_by(name='Member').first()
            new_membership = UserOrganizationRole(
                user_id=join_req.user_id,
                organization_id=org.id,
                role_id=member_role.id
            )
            db.session.add(new_membership)

        db.session.commit()
        flash(f"Approved {join_req.user.username} to join the organization.", "success")
    else:
        flash("This request has already been processed.", "info")

    return redirect(url_for('organization_admin', org_slug=org_slug))


@app.route('/org/<org_slug>/admin/reject-request/<int:request_id>', methods=['POST'])
@login_required
@organization_role_required(['Admin'])
def admin_reject_join_request(org_slug, request_id):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    join_req = JoinRequest.query.filter_by(id=request_id, organization_id=org.id).first_or_404()

    if join_req.status == 'Pending':
        join_req.status = 'Rejected'
        db.session.commit()
        flash(f"Rejected {join_req.user.username}'s join request.", "info")
    else:
        flash("This request has already been processed.", "info")

    return redirect(url_for('organization_admin', org_slug=org_slug))


# --- Site Admin Dashboard ---

@app.route('/site-admin', methods=['GET', 'POST'])
@site_admin_required
def site_admin():
    users = User.query.all()
    return render_template('site_admin.html', users=users)

@app.route('/site-admin/user/<int:user_id>/<action>', methods=['POST'])
@site_admin_required
def site_admin_user_action(user_id, action):
    user = User.query.get_or_404(user_id)
    if action == 'approve':
        user.status = 'Approved'
        flash(f"Approved user {user.username}.", "success")
    elif action == 'suspend':
        user.status = 'Suspended'
        flash(f"Suspended user {user.username}.", "warning")
    elif action == 'make_admin':
        user.is_site_admin = True
        flash(f"Granted Site Admin to {user.username}.", "success")
    elif action == 'remove_admin':
        if user.id == session['user_id']:
            flash("You cannot remove your own admin status.", "error")
        else:
            user.is_site_admin = False
            flash(f"Removed Site Admin from {user.username}.", "success")
    
    db.session.commit()
    return redirect(url_for('site_admin'))

@app.route('/site-admin/create-user', methods=['POST'])
@site_admin_required
def site_admin_create_user():
    username = request.form.get('username').strip()
    email = request.form.get('email').strip()
    password = request.form.get('password')
    
    if not username or not email or not password:
        flash("All fields are required.", "error")
        return redirect(url_for('site_admin'))

    if not re.match(r'^[\w.@+-]+$', username):
        flash("Username contains invalid characters.", "error")
        return redirect(url_for('site_admin'))

    existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
    if existing_user:
        flash("Username or email already exists.", "error")
        return redirect(url_for('site_admin'))

    pw_hash = hash_password(password)
    new_user = User(username=username, email=email, password_hash=pw_hash, is_site_admin=False, status='Approved')
    db.session.add(new_user)
    db.session.commit()
    flash(f"Created new approved user: {username}", "success")
    return redirect(url_for('site_admin'))

@app.route('/site-admin/grab-catalog', methods=['POST'])
@site_admin_required
def site_admin_grab_catalog():
    try:
        pull_fabricator_blueprints()
        flash("Successfully grabbed raw manifest elements from scmdb.net.", "success")
    except Exception as e:
        flash(f"Grabber pipeline exception: {str(e)}", "error")
    return redirect(url_for('site_admin'))

@app.route('/site-admin/parse-catalog', methods=['POST'])
@site_admin_required
def site_admin_parse_catalog():
    try:
        process_blueprints('blueprints unprocessed.txt', 'blueprints.json')
        flash("Successfully compiled dynamic cards into active blueprints.json catalog.", "success")
    except Exception as e:
        flash(f"Parser pipeline exception: {str(e)}", "error")
    return redirect(url_for('site_admin'))


# --- Crafting Queue & Finished Items ---

@app.route('/org/<org_slug>/crafting', methods=['GET'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def crafting_queue(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    jobs = CraftingJob.query.filter_by(organization_id=org.id).order_by(CraftingJob.status.desc(), CraftingJob.completion_time.asc()).all()
    user_role = UserOrganizationRole.query.filter_by(user_id=session['user_id'], organization_id=org.id).first()
    return render_template('crafting.html', org=org, jobs=jobs, user_role=user_role.role.name if user_role else 'Viewer')

@app.route('/org/<org_slug>/crafting/start', methods=['POST'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def start_crafting(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    bp_name = request.form.get('blueprint_name')
    deduct_mode = request.form.get('deduct_mode') # 'auto_lowest', 'auto_highest', 'manual'
    requisition_id = request.form.get('requisition_id')
    notes = request.form.get('notes', '').strip()
    
    blueprints = load_blueprint_catalog()
    bp = next((b for b in blueprints if b['blueprint_name'] == bp_name), None)
    if not bp:
        flash("Blueprint not found.", "error")
        return redirect(url_for('organization_materials', org_slug=org_slug))
        
    required_materials = bp.get('materials', [])
    crafting_time_str = bp.get('crafting_time', '0s')
    
    parsed_reqs = []
    for mat in required_materials:
        amt_str = str(mat.get('amount', '0')).strip()
        match = re.match(r"^([\d.]+)\s*(.*)$", amt_str)
        val = float(match.group(1)) if match else 0.0
        parsed_reqs.append({
            'name': mat.get('name'),
            'amount': val
        })
        
    inventory_dict = {inv.material_name: inv for inv in MaterialInventory.query.filter_by(organization_id=org.id).all()}
    deduction_log = {}
    
    for req in parsed_reqs:
        mat_name = req['name']
        req_amt = req['amount']
        if req_amt <= 0: continue
        
        inv = inventory_dict.get(mat_name)
        if not inv:
            flash(f"Missing material: {mat_name}", "error")
            return redirect(url_for('organization_materials', org_slug=org_slug))
            
        total_avail = inv.grade_baseline + inv.grade_improved + inv.grade_high_quality + inv.grade_exceptional
        if total_avail < req_amt:
            flash(f"Not enough {mat_name}. Need {req_amt}, have {total_avail}.", "error")
            return redirect(url_for('organization_materials', org_slug=org_slug))
            
    grades_lowest_first = ['grade_baseline', 'grade_improved', 'grade_high_quality', 'grade_exceptional']
    grades_highest_first = ['grade_exceptional', 'grade_high_quality', 'grade_improved', 'grade_baseline']
    
    for req in parsed_reqs:
        mat_name = req['name']
        req_amt = req['amount']
        if req_amt <= 0: continue
        
        inv = inventory_dict[mat_name]
        deduction_log[mat_name] = {'baseline': 0, 'improved': 0, 'high_quality': 0, 'exceptional': 0}
        
        if deduct_mode == 'manual':
            for grade in ['baseline', 'improved', 'high_quality', 'exceptional']:
                key = f"mat_{mat_name}_{grade}"
                try:
                    val = float(request.form.get(key, 0))
                except ValueError:
                    val = 0.0
                if val > 0:
                    current_grade_val = getattr(inv, f"grade_{grade}")
                    if current_grade_val < val:
                        flash(f"Manual deduction error: Not enough {grade} grade {mat_name}.", "error")
                        db.session.rollback()
                        return redirect(url_for('organization_materials', org_slug=org_slug))
                    setattr(inv, f"grade_{grade}", current_grade_val - val)
                    deduction_log[mat_name][grade] += val
                    req_amt -= val
            if req_amt > 0.0001: 
                flash(f"Manual deduction error: You didn't allocate enough {mat_name}.", "error")
                db.session.rollback()
                return redirect(url_for('organization_materials', org_slug=org_slug))
        else:
            grades_to_use = grades_lowest_first if deduct_mode == 'auto_lowest' else grades_highest_first
            for grade_col in grades_to_use:
                if req_amt <= 0: break
                avail = getattr(inv, grade_col)
                if avail > 0:
                    take = min(avail, req_amt)
                    setattr(inv, grade_col, avail - take)
                    req_amt -= take
                    grade_key = grade_col.replace('grade_', '')
                    deduction_log[mat_name][grade_key] += take
                    
    time_delta = parse_crafting_time(crafting_time_str)
    now = datetime.datetime.utcnow()
    
    if requisition_id:
        req = Requisition.query.filter_by(id=requisition_id, organization_id=org.id).first()
        if req:
            req.status = 'In Progress'
            req.crafter_id = session.get('user_id')
            if not notes: notes = req.notes # Carry over notes if none provided
    
    job = CraftingJob(
        organization_id=org.id,
        user_id=session.get('user_id'),
        requisition_id=requisition_id if requisition_id else None,
        notes=notes,
        blueprint_name=bp_name,
        status='In Progress',
        start_time=now,
        completion_time=now + time_delta,
        deduction_data=json.dumps(deduction_log)
    )
    db.session.add(job)
    db.session.commit()
    
    flash(f"Started crafting {bp_name}.", "success")
    return redirect(url_for('crafting_queue', org_slug=org_slug))

@app.route('/org/<org_slug>/crafting/edit/<int:job_id>', methods=['POST'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def edit_crafting_job(org_slug, job_id):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    job = CraftingJob.query.filter_by(id=job_id, organization_id=org.id).first_or_404()
    user_role = UserOrganizationRole.query.filter_by(user_id=session['user_id'], organization_id=org.id).first()
    
    # Permission check for destructive actions
    if request.form.get('action') in ['cancel', 'complete', 'adjust_time'] and user_role.role.name not in ['Admin', 'Manager']:
        abort(403)
    
    action = request.form.get('action')
    if action == 'cancel':
        if job.status == 'In Progress':
            if job.deduction_data:
                try:
                    deductions = json.loads(job.deduction_data)
                    for mat_name, grades in deductions.items():
                        inv = MaterialInventory.query.filter_by(organization_id=org.id, material_name=mat_name).first()
                        if inv:
                            inv.grade_baseline += grades.get('baseline', 0)
                            inv.grade_improved += grades.get('improved', 0)
                            inv.grade_high_quality += grades.get('high_quality', 0)
                            inv.grade_exceptional += grades.get('exceptional', 0)
                except Exception as e:
                    app.logger.error(f"Failed to refund materials for job {job.id}: {e}")
            job.status = 'Cancelled'
            if job.requisition_id:
                req = Requisition.query.get(job.requisition_id)
                if req:
                    req.status = 'Pending'
                    req.crafter_id = None
            flash("Job cancelled and materials refunded.", "warning")
            
    elif action == 'complete':
        if job.status == 'In Progress':
            job.status = 'Completed'
            job.completion_time = datetime.datetime.utcnow()
            
            existing = None
            if not job.requisition_id:
                existing = FinishedItem.query.filter_by(organization_id=org.id, blueprint_name=job.blueprint_name, requisition_id=None).first()
                
            if existing:
                existing.quantity += 1
            else:
                fi = FinishedItem(organization_id=org.id, blueprint_name=job.blueprint_name, requisition_id=job.requisition_id, notes=job.notes)
                db.session.add(fi)
            flash("Job marked as completed early.", "success")
            
    elif action == 'adjust_time':
        if job.status == 'In Progress':
            try:
                minutes = int(request.form.get('minutes', 0))
                job.completion_time += timedelta(minutes=minutes)
                flash(f"Adjusted timer by {minutes} minutes.", "success")
            except ValueError:
                pass
                
    elif action == 'remove_history':
        if job.status in ['Completed', 'Cancelled']:
            db.session.delete(job)
            flash("Job removed from history.", "success")
            
    elif action == 'edit_details':
        if job.user_id == session.get('user_id') or user_role.role.name == 'Admin':
            job.notes = request.form.get('notes', '').strip()
            new_bp = request.form.get('blueprint_name')
            if new_bp:
                job.blueprint_name = new_bp
                
            if user_role.role.name in ['Admin', 'Manager']:
                new_status = request.form.get('status')
                if new_status in ['In Progress', 'Completed', 'Cancelled'] and new_status != job.status:
                    if new_status == 'Completed':
                        job.status = 'Completed'
                        job.completion_time = datetime.datetime.utcnow()
                        existing = None
                        if not job.requisition_id:
                            existing = FinishedItem.query.filter_by(organization_id=org.id, blueprint_name=job.blueprint_name, requisition_id=None).first()
                        if existing:
                            existing.quantity += 1
                        else:
                            fi = FinishedItem(organization_id=org.id, blueprint_name=job.blueprint_name, requisition_id=job.requisition_id, notes=job.notes)
                            db.session.add(fi)
                    elif new_status == 'Cancelled' and job.status == 'In Progress':
                        if job.deduction_data:
                            try:
                                deductions = json.loads(job.deduction_data)
                                for mat_name, grades in deductions.items():
                                    inv = MaterialInventory.query.filter_by(organization_id=org.id, material_name=mat_name).first()
                                    if inv:
                                        inv.grade_baseline += grades.get('baseline', 0)
                                        inv.grade_improved += grades.get('improved', 0)
                                        inv.grade_high_quality += grades.get('high_quality', 0)
                                        inv.grade_exceptional += grades.get('exceptional', 0)
                            except Exception as e:
                                app.logger.error(f"Failed to refund materials for job {job.id}: {e}")
                        job.status = 'Cancelled'
                        if job.requisition_id:
                            req = Requisition.query.get(job.requisition_id)
                            if req:
                                req.status = 'Pending'
                                req.crafter_id = None
                    else:
                        job.status = new_status

            adjust_minutes = request.form.get('adjust_minutes')
            if adjust_minutes and job.status == 'In Progress':
                try:
                    mins = int(adjust_minutes)
                    job.completion_time += timedelta(minutes=mins)
                except ValueError:
                    pass
                    
            flash("Job details updated.", "success")
        else:
            flash("You don't have permission to edit this job.", "error")
                
    db.session.commit()
    return redirect(url_for('crafting_queue', org_slug=org_slug))

@app.route('/org/<org_slug>/finished-items', methods=['GET', 'POST'])
@login_required
@organization_role_required(['Admin', 'Manager', 'Member'])
def finished_items(org_slug):
    org = Organization.query.filter_by(slug=org_slug).first_or_404()
    user_role = UserOrganizationRole.query.filter_by(user_id=session['user_id'], organization_id=org.id).first()
    
    if request.method == 'POST':
        if user_role.role.name == 'Member':
            abort(403)
        item_id = request.form.get('item_id')
        qty_to_remove = int(request.form.get('remove_qty', 1))
        item = FinishedItem.query.filter_by(id=item_id, organization_id=org.id).first_or_404()
        
        action = request.form.get('action', 'remove')
        
        if action == 'distribute':
            if item.requisition_id:
                req = Requisition.query.get(item.requisition_id)
                if req:
                    req.status = 'Fulfilled'
            if item.quantity > qty_to_remove:
                item.quantity -= qty_to_remove
                flash(f"Distributed {qty_to_remove} of {item.blueprint_name}.", "success")
            else:
                db.session.delete(item)
                flash(f"Distributed all of {item.blueprint_name}.", "success")
        elif action == 'requeue_lost':
            if item.requisition_id:
                req = Requisition.query.get(item.requisition_id)
                if req:
                    req.status = 'Pending'
                    req.crafter_id = None
            db.session.delete(item)
            db.session.commit()
            flash("Item removed from Finished queue. Please re-craft it.", "info")
            return redirect(url_for('organization_materials', org_slug=org_slug, requeue_bp=item.blueprint_name, requeue_req=item.requisition_id))
        elif action == 'requeue_not_lost':
            bp_name = item.blueprint_name
            req_id = item.requisition_id
            notes = item.notes
            
            blueprints = load_blueprint_catalog()
            bp = next((b for b in blueprints if b['blueprint_name'] == bp_name), None)
            time_delta = parse_crafting_time(bp.get('crafting_time', '0s')) if bp else timedelta()
            now = datetime.datetime.utcnow()
            
            if req_id:
                req = Requisition.query.get(req_id)
                if req:
                    req.status = 'In Progress'
                    req.crafter_id = session.get('user_id')
                    
            job = CraftingJob(
                organization_id=org.id,
                user_id=session.get('user_id'),
                requisition_id=req_id,
                blueprint_name=bp_name,
                status='In Progress',
                notes=notes,
                start_time=now,
                completion_time=now + time_delta,
                deduction_data="{}"
            )
            db.session.add(job)
            
            if item.quantity > 1:
                item.quantity -= 1
            else:
                db.session.delete(item)
                
            db.session.commit()
            flash(f"Re-crafting {bp_name} without new material deduction.", "success")
            return redirect(url_for('crafting_queue', org_slug=org_slug))

        else: # remove
            if qty_to_remove >= item.quantity:
                db.session.delete(item)
                flash(f"Removed all of {item.blueprint_name}.", "success")
            else:
                item.quantity -= qty_to_remove
                flash(f"Removed {qty_to_remove} of {item.blueprint_name}.", "success")
                
        db.session.commit()
        return redirect(url_for('finished_items', org_slug=org_slug))
        
    items = FinishedItem.query.filter_by(organization_id=org.id).order_by(FinishedItem.created_at.desc()).all()
    return render_template('finished_items.html', org=org, items=items, user_role=user_role.role.name)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
