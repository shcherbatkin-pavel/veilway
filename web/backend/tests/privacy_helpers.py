"""Inspect only disposable test databases; never print material on failure."""
import sqlalchemy as sa


def assert_database_excludes(factory, forbidden):
    with factory() as db:
        metadata = sa.MetaData()
        metadata.reflect(bind=db.connection())
        for table in metadata.sorted_tables:
            for row in db.execute(sa.select(table)):
                for value in row:
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="ignore")
                    if isinstance(value, str):
                        if any(marker in value for marker in forbidden):
                            raise AssertionError("sensitive material persisted in application database")
