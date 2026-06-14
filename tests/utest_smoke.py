"""Smoke tests — verify the app factory boots and basic routes respond."""
import os
import tempfile
import unittest


class AppSmokeTest(unittest.TestCase):

    def setUp(self):
        # Use a temp dir for Flask-Session so the filesystem backend can write.
        self._session_dir = tempfile.mkdtemp()
        os.environ.setdefault('SECRET_KEY', 'ci-test-secret')
        os.environ['SESSION_FILE_DIR'] = self._session_dir
        os.environ['TESTING'] = '1'
        # In-memory SQLite: no Postgres required in CI.
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

        # Import after env vars are set so create_app picks them up.
        from vstabletop import create_app
        self.app = create_app()

        from vstabletop.models import db
        with self.app.app_context():
            db.create_all()

        self.client = self.app.test_client()

    def test_health_returns_200(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn('status', data)

    def test_login_page_loads(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_game_routes_redirect(self):
        for game_num in range(1, 11):
            with self.subTest(game=game_num):
                response = self.client.get(f'/games/{game_num}')
                # login_required redirects to login page (302)
                self.assertEqual(response.status_code, 302)


if __name__ == '__main__':
    unittest.main()
