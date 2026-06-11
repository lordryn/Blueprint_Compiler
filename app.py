import os
import json
import hashlib
from flask import Flask, render_template, request, redirect, url_for, session, abort, flash, jsonify
from models import db, User, Organization, Role, UserOrganizationRole, Claim, Requisition, JoinRequest
from bp_catalog_grabber import pull_fabricator_blueprints
from blueprint_parser import process_blueprints

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///crafters.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)


# Helper function for password hashing using hashlib
def hash_password(password):
    salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ":" + pw_hash.hex()


def check_password(stored_hash, password):
    try:
        salt_hex, hash_hex = stored_hash.split(":")
        salt = bytes.fromhex(salt_hex)
        pw_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return pw_hash.hex() == hash_hex
    except Exception:
        return False


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


# Database initialization wrapper
@app.before_request
def setup_db():
    db.create_all()

    # Initialize basic Roles if empty
    if Role.query.count() == 0:
        admin_role = Role(name='Admin', description='Full control over membership, roles, and grabber')
        manager_role = Role(name='Manager', description='Control claims and requisitions')
        member_role = Role(name='Member', description='Standard user, can make claims and view lists')
        viewer_role = Role(name='Viewer', description='Read-only observer')
        db.session.add_all([admin_role, manager_role, member_role, viewer_role])
        db.session.commit()


# Master Blueprint data loader
def load_blueprint_catalog():
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
        with open(catalog_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


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
            session['user_id'] = user.id
            session['username'] = user.username
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

        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            flash("Username or email already exists.", "error")
            return redirect(url_for('register'))

        pw_hash = hash_password(password)
        new_user = User(username=username, email=email, password_hash=pw_hash)
        db.session.add(new_user)
        db.session.commit()

        session['user_id'] = new_user.id
        session['username'] = new_user.username
        flash("Account created successfully!", "success")
        return redirect(url_for('index'))
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
        crafters_map[c.blueprint_name].append(c.user.username)

    # Requisitions
    org_reqs = Requisition.query.filter_by(organization_id=org.id, status='Pending').all()

    return render_template('dashboard.html',
                           org=org,
                           blueprints=blueprints,
                           categories=categories,
                           crafters_map=crafters_map,
                           requisitions=org_reqs,
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

    new_req = Requisition(
        user_id=session['user_id'],
        organization_id=org.id,
        blueprint_name=blueprint_name,
        quantity=qty
    )
    db.session.add(new_req)
    db.session.commit()
    flash("Requisition submitted to organization crafters.", "success")
    return redirect(url_for('organization_dashboard', org_slug=org_slug))


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


# --- Admin Tools: Unified Catalog Grabber and Parser Interfaces ---

@app.route('/org/<org_slug>/admin/grab-catalog', methods=['POST'])
@login_required
@organization_role_required(['Admin'])
def admin_grab_catalog(org_slug):
    try:
        pull_fabricator_blueprints()
        flash("Successfully grabbed raw manifest elements from scmdb.net.", "success")
    except Exception as e:
        flash(f"Grabber pipeline exception: {str(e)}", "error")

    return redirect(url_for('organization_admin', org_slug=org_slug))


@app.route('/org/<org_slug>/admin/parse-catalog', methods=['POST'])
@login_required
@organization_role_required(['Admin'])
def admin_parse_catalog(org_slug):
    try:
        process_blueprints('blueprints unprocessed.txt', 'blueprints.json')
        flash("Successfully compiled dynamic cards into active blueprints.json catalog.", "success")
    except Exception as e:
        flash(f"Parser pipeline exception: {str(e)}", "error")

    return redirect(url_for('organization_admin', org_slug=org_slug))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
