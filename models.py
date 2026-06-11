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

    def __repr__(self):
        return f"<Organization {self.name}>"


class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
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
    status = db.Column(db.String(20), default='Pending')  # 'Pending', 'Fulfilled', 'Cancelled'
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', back_populates='requisitions')
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
