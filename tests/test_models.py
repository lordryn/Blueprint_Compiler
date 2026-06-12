from models import User, Organization, Role, UserOrganizationRole

def test_user_creation(db):
    user = User(username="testuser", email="test@example.com", password_hash="hash")
    db.session.add(user)
    db.session.commit()
    
    saved_user = User.query.filter_by(username="testuser").first()
    assert saved_user is not None
    assert saved_user.email == "test@example.com"

def test_organization_creation(db):
    org = Organization(name="Test Org", slug="test-org")
    db.session.add(org)
    db.session.commit()
    
    saved_org = Organization.query.filter_by(slug="test-org").first()
    assert saved_org is not None
    assert saved_org.name == "Test Org"

def test_role_creation(db):
    role = Role(name="Admin", description="Admin role")
    db.session.add(role)
    db.session.commit()
    
    saved_role = Role.query.filter_by(name="Admin").first()
    assert saved_role is not None

def test_user_organization_role(db):
    user = User(username="testuser", email="test@example.com", password_hash="hash")
    org = Organization(name="Test Org", slug="test-org")
    role = Role(name="Admin", description="Admin role")
    
    db.session.add_all([user, org, role])
    db.session.commit()
    
    user_org_role = UserOrganizationRole(user_id=user.id, organization_id=org.id, role_id=role.id)
    db.session.add(user_org_role)
    db.session.commit()
    
    saved_uor = UserOrganizationRole.query.first()
    assert saved_uor is not None
    assert saved_uor.user.username == "testuser"
    assert saved_uor.organization.name == "Test Org"
    assert saved_uor.role.name == "Admin"
