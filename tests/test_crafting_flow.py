import pytest
from app import db as _db
from models import User, Organization, Role, UserOrganizationRole, MaterialInventory, CraftingJob, Requisition, FinishedItem
from unittest.mock import patch

@pytest.fixture
def test_bp_catalog():
    return [
        {
            "blueprint_name": "Test Blueprint",
            "category": "Test",
            "materials": [
                {
                    "name": "Titanium",
                    "amount": "2 SCU"
                }
            ],
            "crafting_time": "1s"
        }
    ]

def test_full_crafting_flow(client, db, test_bp_catalog):
    # 1. Setup Data
    # Register user
    client.post('/register', data={
        'username': 'crafter',
        'email': 'crafter@test.com',
        'password': 'password123'
    }, follow_redirects=True)
    
    # Login
    client.post('/login', data={
        'username_or_email': 'crafter',
        'password': 'password123'
    }, follow_redirects=True)
    
    user = User.query.filter_by(username='crafter').first()
    
    # Create Org
    org = Organization(name='Test Org', slug='test-org')
    db.session.add(org)
    
    # Check or Create Role
    role = Role.query.filter_by(name='Admin').first()
    if not role:
        role = Role(name='Admin', description='Admin role')
        db.session.add(role)
        
    db.session.commit()
    
    # Add User to Org
    uor = UserOrganizationRole(user_id=user.id, organization_id=org.id, role_id=role.id)
    db.session.add(uor)
    
    # Add Inventory
    inv = MaterialInventory(organization_id=org.id, material_name='Titanium', grade_baseline=10.0)
    db.session.add(inv)
    
    # Create Requisition
    req = Requisition(user_id=user.id, organization_id=org.id, blueprint_name='Test Blueprint', quantity=1)
    db.session.add(req)
    db.session.commit()
    
    req_id = req.id
    
    with patch('app.load_blueprint_catalog', return_value=test_bp_catalog):
        # 2. Start Crafting Job
        response = client.post('/org/test-org/crafting/start', data={
            'blueprint_name': 'Test Blueprint',
            'deduct_mode': 'auto_lowest',
            'requisition_id': req_id,
            'notes': 'Test craft'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        assert b"Started crafting Test Blueprint" in response.data
        
        # Check Job Created
        job = CraftingJob.query.filter_by(organization_id=org.id, blueprint_name='Test Blueprint').first()
        assert job is not None
        assert job.status == 'In Progress'
        
        # Check Inventory Deducted
        inv = MaterialInventory.query.filter_by(organization_id=org.id, material_name='Titanium').first()
        assert inv.grade_baseline == 8.0  # 10.0 - 2.0
        
        # Check Requisition updated
        req = Requisition.query.get(req_id)
        assert req.status == 'In Progress'
        assert req.crafter_id == user.id
        
        # 3. Complete Crafting Job
        response = client.post(f'/org/test-org/crafting/edit/{job.id}', data={
            'action': 'complete'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        job = CraftingJob.query.get(job.id)
        assert job.status == 'Completed'
        
        # Check Finished Item
        item = FinishedItem.query.filter_by(organization_id=org.id, blueprint_name='Test Blueprint').first()
        assert item is not None
        assert item.quantity == 1
        
        # 4. Distribute Item
        response = client.post('/org/test-org/finished-items', data={
            'item_id': item.id,
            'remove_qty': '1',
            'action': 'distribute'
        }, follow_redirects=True)
        
        assert response.status_code == 200
        
        # Check Requisition Fulfilled
        req = Requisition.query.get(req_id)
        assert req.status == 'Fulfilled'
        
        # Check Item Removed
        item = FinishedItem.query.filter_by(organization_id=org.id, blueprint_name='Test Blueprint').first()
        assert item is None
