import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Organization(db.Model):
    __tablename__ = 'organizations'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)  # For custom URLs e.g., /org/wyvern
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    user_roles = db.relationship('UserOrganizationRole', back_populates='organization', cascade='all, delete-orphan')
    claims = db.relationship('Claim', back_populates='organization', cascade='all, delete-orphan')
    requisitions = db.relationship('Requisition', back_populates='organization', cascade='all, delete-orphan')
    join_requests = db.relationship('JoinRequest', back_populates='organization', cascade='all, delete-orphan')
    inventory = db.relationship('MaterialInventory', back_populates='organization', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Organization {self.name}>"


class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    is_site_admin = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='Pending')  # 'Pending', 'Approved', 'Suspended'
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    org_roles = db.relationship('UserOrganizationRole', back_populates='user', cascade='all, delete-orphan')
    claims = db.relationship('Claim', back_populates='user', cascade='all, delete-orphan')
    requisitions = db.relationship('Requisition', back_populates='user', cascade='all, delete-orphan')
    join_requests = db.relationship('JoinRequest', back_populates='user', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<User {self.username}>"


class Role(db.Model):
    __tablename__ = 'roles'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # 'Admin', 'Manager', 'Member', 'Viewer'
    description = db.Column(db.String(255))
    
    def __repr__(self):
        return f"<Role {self.name}>"


class UserOrganizationRole(db.Model):
    """
    Association table defining a User's role inside a specific Organization.
    This enables a single user to be a Member in one organization, and an Admin in another.
    """
    __tablename__ = 'user_organization_roles'
    
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id', ondelete='RESTRICT'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='org_roles')
    organization = db.relationship('Organization', back_populates='user_roles')
    role = db.relationship('Role')

    def __repr__(self):
        return f"<UserOrganizationRole User:{self.user_id} Org:{self.organization_id} Role:{self.role_id}>"


class Claim(db.Model):
    """
    Tracks which member in which organization can craft which blueprint.
    Uses the canonical blueprint_name string from the master blueprints catalog (blueprints.json).
    """
    __tablename__ = 'claims'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    blueprint_name = db.Column(db.String(250), nullable=False)  # Matches blueprint_name in blueprints.json
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='claims')
    organization = db.relationship('Organization', back_populates='claims')

    def __repr__(self):
        return f"<Claim User:{self.user_id} Org:{self.organization_id} BlueprintName:{self.blueprint_name}>"


class Requisition(db.Model):
    """
    Tracks active group requests: Who needs what items.
    Uses the canonical blueprint_name string.
    """
    __tablename__ = 'requisitions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    blueprint_name = db.Column(db.String(250), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='Pending')  # 'Pending', 'In Progress', 'Fulfilled', 'Cancelled'
    notes = db.Column(db.Text, nullable=True)
    crafter_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], back_populates='requisitions')
    crafter = db.relationship('User', foreign_keys=[crafter_id])
    organization = db.relationship('Organization', back_populates='requisitions')

    def __repr__(self):
        return f"<Requisition User:{self.user_id} Org:{self.organization_id} BlueprintName:{self.blueprint_name} Status:{self.status}>"


class JoinRequest(db.Model):
    """
    Tracks user join requests for specific organization URL slugs.
    """
    __tablename__ = 'join_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    status = db.Column(db.String(20), default='Pending')  # 'Pending', 'Approved', 'Rejected'
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='join_requests')
    organization = db.relationship('Organization', back_populates='join_requests')

    def __repr__(self):
        return f"<JoinRequest User:{self.user_id} Org:{self.organization_id} Status:{self.status}>"

class MaterialInventory(db.Model):
    """
    Tracks raw material inventory for an organization by grade.
    """
    __tablename__ = 'material_inventory'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    material_name = db.Column(db.String(250), nullable=False)
    grade_baseline = db.Column(db.Float, default=0.0)      # 500Q
    grade_improved = db.Column(db.Float, default=0.0)      # 501Q-749Q
    grade_high_quality = db.Column(db.Float, default=0.0)  # 750Q-899Q
    grade_exceptional = db.Column(db.Float, default=0.0)   # 900Q-1000Q
    last_updated = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', back_populates='inventory')

    def __repr__(self):
        return f"<MaterialInventory Org:{self.organization_id} Material:{self.material_name}>"


class CraftingJob(db.Model):
    """
    Tracks an active crafting job, acting as a background timer.
    """
    __tablename__ = 'crafting_jobs'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    requisition_id = db.Column(db.Integer, db.ForeignKey('requisitions.id', ondelete='SET NULL'), nullable=True)
    blueprint_name = db.Column(db.String(250), nullable=False)
    status = db.Column(db.String(50), default='In Progress')  # 'In Progress', 'Completed', 'Cancelled'
    notes = db.Column(db.Text, nullable=True)
    start_time = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    completion_time = db.Column(db.DateTime, nullable=False)
    
    # Store JSON string of what materials were deducted for easy refunding if cancelled
    deduction_data = db.Column(db.Text, nullable=True)
    
    organization = db.relationship('Organization', backref=db.backref('crafting_jobs', lazy=True, cascade="all, delete-orphan"))
    user = db.relationship('User', backref=db.backref('crafting_jobs', lazy=True))
    requisition = db.relationship('Requisition', backref=db.backref('crafting_jobs', lazy=True))

    def __repr__(self):
        return f"<CraftingJob ID:{self.id} Org:{self.organization_id} BP:{self.blueprint_name} Status:{self.status}>"


class FinishedItem(db.Model):
    """
    Inventory for completed items.
    """
    __tablename__ = 'finished_items'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    requisition_id = db.Column(db.Integer, db.ForeignKey('requisitions.id', ondelete='SET NULL'), nullable=True)
    blueprint_name = db.Column(db.String(250), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    location = db.Column(db.String(250), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    organization = db.relationship('Organization', backref=db.backref('finished_items', lazy=True, cascade="all, delete-orphan"))
    requisition = db.relationship('Requisition', backref=db.backref('finished_items', lazy=True))

    def __repr__(self):
        return f"<FinishedItem Org:{self.organization_id} Item:{self.blueprint_name} Qty:{self.quantity}>"
