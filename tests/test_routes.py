from models import User

def test_index_redirects_unauthenticated(client):
    response = client.get('/')
    # Should redirect to login
    assert response.status_code == 302
    assert '/login' in response.headers.get('Location', '')

def test_login_page_renders(client):
    response = client.get('/login')
    assert response.status_code == 200
    assert b"Login" in response.data

def test_register_page_renders(client):
    response = client.get('/register')
    assert response.status_code == 200
    assert b"Register" in response.data

def test_register_creates_user(client, db):
    response = client.post('/register', data={
        'username': 'newuser',
        'email': 'new@example.com',
        'password': 'password123'
    }, follow_redirects=True)
    
    assert response.status_code == 200
    # First user is auto-approved and site admin
    user = User.query.filter_by(username='newuser').first()
    assert user is not None
    assert user.is_site_admin is True
    assert user.status == 'Approved'

def test_login_authenticates_user(client, db):
    # Register first
    client.post('/register', data={
        'username': 'logintest',
        'email': 'login@example.com',
        'password': 'password123'
    })
    
    # Try to login
    response = client.post('/login', data={
        'username_or_email': 'logintest',
        'password': 'password123'
    }, follow_redirects=True)
    
    assert response.status_code == 200
    # Should be at index, not login anymore
    assert b"Welcome back!" in response.data
