\getenv app_password APP_DB_PASSWORD
CREATE ROLE boutique_app LOGIN PASSWORD :'app_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
