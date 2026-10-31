from django.core.cache import cache
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from accounts.models import Project, ProjectUser

User = get_user_model()


class SignUpViewTests(TestCase):
	"""Test cases for user signup functionality"""
	
	def setUp(self):
		"""Set up test client"""
		cache.clear()  # auth throttle counts live in Redis across tests
		self.client = APIClient()
		self.signup_url = "/api/auth/signup/"
	
	def test_signup_success(self):
		"""Test successful user signup"""
		data = {
			"first_name": "John",
			"last_name": "Doe",
			"email": "john.doe@example.com",
			"password": "testpass123",
			"terms_version": "2026-10-29"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		
		# Check response structure
		self.assertTrue(response.json()["success"])
		self.assertEqual(response.json()["message"], "User created successfully")
		self.assertIn("data", response.json())
		self.assertIn("user", response.json()["data"])
		
		# Check user was created
		user = User.objects.get(email="john.doe@example.com")
		self.assertEqual(user.first_name, "John")
		self.assertEqual(user.last_name, "Doe")
		self.assertEqual(user.username, "john.doe@example.com")
		
		# Check default project was created
		project_user = ProjectUser.objects.filter(user=user).first()
		self.assertIsNotNone(project_user)
		self.assertEqual(project_user.role, "admin")
		self.assertEqual(project_user.project.name, "Default Project")
		
		# Check user data in response
		user_data = response.json()["data"]["user"]
		self.assertEqual(user_data["id"], str(user.id))
		self.assertEqual(user_data["first_name"], "John")
	
	def test_signup_duplicate_email(self):
		"""Test signup with duplicate email fails"""
		# Create existing user
		User.objects.create_user(
			username="existing@example.com",
			email="existing@example.com",
			password="password123"
		)
		
		data = {
			"first_name": "Jane",
			"last_name": "Doe",
			"email": "existing@example.com",
			"password": "testpass123",
			"terms_version": "2026-10-29"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		
		# Check error message (custom exception handler wraps errors)
		self.assertFalse(response.json()["success"])
		self.assertIn("error", response.json())
		self.assertIn("details", response.json()["error"])
		self.assertIn("email", response.json()["error"]["details"])
	
	def test_signup_missing_fields(self):
		"""Test signup with missing required fields"""
		# Missing email
		data = {
			"first_name": "John",
			"last_name": "Doe",
			"password": "testpass123",
			"terms_version": "2026-10-29"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("email", response.json()["error"]["details"])
		
		# Missing password
		data = {
			"first_name": "John",
			"last_name": "Doe",
			"email": "john@example.com"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("password", response.json()["error"]["details"])
		
		# Missing first_name
		data = {
			"last_name": "Doe",
			"email": "john@example.com",
			"password": "testpass123",
			"terms_version": "2026-10-29"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("first_name", response.json()["error"]["details"])
		
		# Missing last_name
		data = {
			"first_name": "John",
			"email": "john@example.com",
			"password": "testpass123",
			"terms_version": "2026-10-29"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("last_name", response.json()["error"]["details"])
	
	def test_signup_invalid_email(self):
		"""Test signup with invalid email format"""
		data = {
			"first_name": "John",
			"last_name": "Doe",
			"email": "invalid-email",
			"password": "testpass123",
			"terms_version": "2026-10-29"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("email", response.json()["error"]["details"])
	
	def test_signup_password_too_short(self):
		"""Test signup with password shorter than 8 characters"""
		data = {
			"first_name": "John",
			"last_name": "Doe",
			"email": "john@example.com",
			"password": "short"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("password", response.json()["error"]["details"])
	
	def test_signup_creates_default_project(self):
		"""Test that signup creates a default project for the user"""
		data = {
			"first_name": "John",
			"last_name": "Doe",
			"email": "john@example.com",
			"password": "testpass123",
			"terms_version": "2026-10-29"
		}
		
		response = self.client.post(self.signup_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		
		user = User.objects.get(email="john@example.com")
		project_user = ProjectUser.objects.get(user=user)
		
		self.assertIsNotNone(project_user.project)
		self.assertEqual(project_user.project.name, "Default Project")
		self.assertIsNotNone(project_user.project.slug)


class LoginViewTests(TestCase):
	"""Test cases for user login functionality"""
	
	def setUp(self):
		"""Set up test client and test user"""
		cache.clear()  # auth throttle counts live in Redis across tests
		self.client = APIClient()
		self.login_url = "/api/auth/login/"
		
		# Create a test user
		self.user = User.objects.create_user(
			username="testuser@example.com",
			email="testuser@example.com",
			password="testpass123",
			first_name="Test",
			last_name="User"
		)
		
		# Create a default project for the user
		project = Project.objects.create(name="Test Project", slug="test-project-123")
		ProjectUser.objects.create(project=project, user=self.user, role="admin")
	
	def test_login_success(self):
		"""Test successful login"""
		data = {
			"email": "testuser@example.com",
			"password": "testpass123"
		}
		
		response = self.client.post(self.login_url, data, format="json")
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		
		# Check response structure
		self.assertTrue(response.json()["success"])
		self.assertEqual(response.json()["message"], "Login successful")
		self.assertIn("data", response.json())
		self.assertIn("user", response.json()["data"])
		self.assertIn("projects", response.json()["data"])
		
		# Check user data
		user_data = response.json()["data"]["user"]
		self.assertEqual(user_data["id"], str(self.user.id))
		self.assertEqual(user_data["first_name"], "Test")
		
		# Check projects data
		projects_data = response.json()["data"]["projects"]
		self.assertEqual(len(projects_data), 1)
		self.assertEqual(projects_data[0]["name"], "Test Project")
		self.assertEqual(projects_data[0]["role"], "admin")
	
	def test_login_invalid_email(self):
		"""Test login with non-existent email"""
		data = {
			"email": "nonexistent@example.com",
			"password": "testpass123"
		}
		
		response = self.client.post(self.login_url, data, format="json")
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		
		# Check error message (custom exception handler wraps errors)
		self.assertFalse(response.json()["success"])
		self.assertIn("error", response.json())
		self.assertIn("details", response.json()["error"])
		self.assertIn("non_field_errors", response.json()["error"]["details"])
	
	def test_login_invalid_password(self):
		"""Test login with incorrect password"""
		data = {
			"email": "testuser@example.com",
			"password": "wrongpassword"
		}
		
		response = self.client.post(self.login_url, data, format="json")
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		
		# Check error message (custom exception handler wraps errors)
		self.assertFalse(response.json()["success"])
		self.assertIn("error", response.json())
		self.assertIn("details", response.json()["error"])
		self.assertIn("non_field_errors", response.json()["error"]["details"])
	
	def test_login_missing_email(self):
		"""Test login with missing email"""
		data = {
			"password": "testpass123"
		}
		
		response = self.client.post(self.login_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("email", response.json()["error"]["details"])
	
	def test_login_missing_password(self):
		"""Test login with missing password"""
		data = {
			"email": "testuser@example.com"
		}
		
		response = self.client.post(self.login_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("password", response.json()["error"]["details"])
	
	def test_login_inactive_user(self):
		"""Test login with inactive user account"""
		# Deactivate user
		self.user.is_active = False
		self.user.save()
		
		data = {
			"email": "testuser@example.com",
			"password": "testpass123"
		}
		
		response = self.client.post(self.login_url, data, format="json")
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		
		# Check error message (custom exception handler wraps errors)
		self.assertFalse(response.json()["success"])
		self.assertIn("error", response.json())
		self.assertIn("details", response.json()["error"])
		self.assertIn("non_field_errors", response.json()["error"]["details"])
	
	def test_login_invalid_email_format(self):
		"""Test login with invalid email format"""
		data = {
			"email": "invalid-email",
			"password": "testpass123"
		}
		
		response = self.client.post(self.login_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("email", response.json()["error"]["details"])
	
	def test_login_returns_user_projects(self):
		"""Test that login returns all user's projects"""
		# Create additional project for the user
		project2 = Project.objects.create(name="Project 2", slug="project-2-123")
		ProjectUser.objects.create(project=project2, user=self.user, role="member")
		
		data = {
			"email": "testuser@example.com",
			"password": "testpass123"
		}
		
		response = self.client.post(self.login_url, data, format="json")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		
		projects_data = response.json()["data"]["projects"]
		self.assertEqual(len(projects_data), 2)
		
		# Check both projects are returned
		project_names = [p["name"] for p in projects_data]
		self.assertIn("Test Project", project_names)
		self.assertIn("Project 2", project_names)


class AuthThrottleTests(TestCase):
	def setUp(self):
		cache.clear()
		self.client = APIClient()

	@override_settings(AUTH_THROTTLE_RATE="2/min")
	def test_login_attempts_are_capped_per_ip(self):
		data = {"email": "nobody@example.com", "password": "wrong-password"}
		codes = [self.client.post("/api/auth/login/", data, format="json").status_code for _ in range(3)]
		self.assertEqual(codes, [400, 400, 429])



class SignUpConsentTests(TestCase):
	def setUp(self):
		cache.clear()
		self.client = APIClient()
		self.data = {"first_name": "Ada", "last_name": "L", "email": "ada@example.com", "password": "testpass123"}

	def test_signup_requires_terms_acceptance(self):
		response = self.client.post("/api/auth/signup/", self.data, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("terms_version", response.json()["error"]["details"])
		self.assertFalse(User.objects.filter(email="ada@example.com").exists())

	def test_signup_records_accepted_terms(self):
		response = self.client.post("/api/auth/signup/", {**self.data, "terms_version": "2026-10-29"}, format="json")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		user = User.objects.get(email="ada@example.com")
		self.assertEqual(user.terms_version, "2026-10-29")
		self.assertIsNotNone(user.terms_accepted_at)
