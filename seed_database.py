"""Create the MySQL schema and seed the configured first administrator."""
import os

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy import inspect, text

load_dotenv()


def seed():
    database_url = make_url(os.getenv(
        'DATABASE_URL', 'mysql+pymysql://root:@127.0.0.1:3306/menti_chatbot?charset=utf8mb4'))
    if database_url.drivername.startswith('mysql'):
        import pymysql
        connection_args = {
            'host': database_url.host or '127.0.0.1',
            'port': database_url.port or 3306,
            'user': database_url.username or '',
            'password': database_url.password or '',
            'charset': 'utf8mb4',
            'connect_timeout': 5,
        }
        database_name = database_url.database
        if not database_name:
            raise RuntimeError('DATABASE_URL must include a database name')
        try:
            connection = pymysql.connect(**connection_args, database=database_name)
            connection.close()
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1049:
                raise
            connection = pymysql.connect(**connection_args)
            try:
                with connection.cursor() as cursor:
                    safe_name = database_name.replace('`', '``')
                    cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{safe_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            finally:
                connection.close()

    # Import after database creation because app startup creates its tables.
    from app import app
    from database import Account, apply_additive_schema_updates, create_account, db, promote_initial_admin

    with app.app_context():
        db.create_all()
        apply_additive_schema_updates()
        # Remove only obsolete migration tables, and only when provably empty.
        # Never silently discard any transitional MySQL rows.
        inspector = inspect(db.engine)
        legacy_tables = [name for name in inspector.get_table_names()
                         if name in ("app_documents", "record_attributes") or name.endswith("_attributes")]
        for legacy_table in legacy_tables:
            if legacy_table not in inspector.get_table_names():
                continue
            old_rows = db.session.execute(text(f"SELECT COUNT(*) FROM `{legacy_table}`")).scalar_one()
            if old_rows:
                raise RuntimeError(
                    f"Refusing to remove {legacy_table}: it contains {old_rows} rows. "
                    "Export/convert those MySQL records before rerunning schema cleanup."
                )
            db.session.execute(text(f"DROP TABLE `{legacy_table}`"))
            db.session.commit()
            print(f"Removed empty legacy table {legacy_table}.")
        admin_email = os.getenv('ADMIN_EMAIL', '').strip().lower()
        if not admin_email:
            print('Schema ready. ADMIN_EMAIL is empty; no administrator was seeded.')
            return
        admin_password = os.getenv('ADMIN_PASSWORD', '')
        account = Account.query.filter_by(email=admin_email).first()
        if not account:
            account = create_account(admin_email, admin_password or None,
                                     display_name=admin_email.split('@')[0])
        elif admin_password:
            account.set_password(admin_password)
        promote_initial_admin(account, admin_email)
        print(f'MySQL schema ready; email/password administrator provisioned for {admin_email}.')


if __name__ == '__main__':
    seed()
