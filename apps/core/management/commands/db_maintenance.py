"""
Database Maintenance Command
Runs VACUUM, ANALYZE, and other database optimization tasks
"""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Run database maintenance tasks (vacuum, analyze, reindex)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--vacuum",
            action="store_true",
            help="Run VACUUM ANALYZE (PostgreSQL only)",
        )
        parser.add_argument(
            "--reindex",
            action="store_true",
            help="Reindex all tables (PostgreSQL only)",
        )
        parser.add_argument(
            "--stats",
            action="store_true",
            help="Show database statistics",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting database maintenance..."))

        if options["vacuum"]:
            self.run_vacuum()

        if options["reindex"]:
            self.run_reindex()

        if options["stats"]:
            self.show_stats()

        if not any([options["vacuum"], options["reindex"], options["stats"]]):
            # Run all by default
            self.run_vacuum()
            self.show_stats()

        self.stdout.write(self.style.SUCCESS("Database maintenance completed!"))

    def run_vacuum(self):
        """Run VACUUM ANALYZE on PostgreSQL"""
        self.stdout.write("Running VACUUM ANALYZE...")

        try:
            with connection.cursor() as cursor:
                # Check if PostgreSQL
                cursor.execute("SELECT version();")
                version = cursor.fetchone()[0]

                if "PostgreSQL" in version:
                    cursor.execute("VACUUM ANALYZE;")
                    self.stdout.write(self.style.SUCCESS("✓ VACUUM ANALYZE completed"))
                else:
                    self.stdout.write(
                        self.style.WARNING("VACUUM only supported on PostgreSQL")
                    )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ VACUUM failed: {e}"))

    def run_reindex(self):
        """Reindex all tables"""
        self.stdout.write("Reindexing tables...")

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version();")
                version = cursor.fetchone()[0]

                if "PostgreSQL" in version:
                    cursor.execute("REINDEX DATABASE CONCURRENTLY;")
                    self.stdout.write(self.style.SUCCESS("✓ Reindex completed"))
                else:
                    self.stdout.write(
                        self.style.WARNING("REINDEX only supported on PostgreSQL")
                    )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Reindex failed: {e}"))

    def show_stats(self):
        """Show database statistics"""
        self.stdout.write("\nDatabase Statistics:")
        self.stdout.write("=" * 60)

        try:
            with connection.cursor() as cursor:
                # Table sizes
                cursor.execute("""
                    SELECT
                        schemaname,
                        tablename,
                        pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
                    FROM pg_tables
                    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
                    LIMIT 10;
                """)

                self.stdout.write("\nTop 10 Largest Tables:")
                for schema, table, size in cursor.fetchall():
                    self.stdout.write(f"  {schema}.{table}: {size}")

                # Index usage
                cursor.execute("""
                    SELECT
                        schemaname,
                        tablename,
                        indexname,
                        idx_scan,
                        idx_tup_read,
                        idx_tup_fetch
                    FROM pg_stat_user_indexes
                    WHERE idx_scan = 0
                    LIMIT 10;
                """)

                unused_indexes = cursor.fetchall()
                if unused_indexes:
                    self.stdout.write("\nUnused Indexes (consider dropping):")
                    for schema, table, index, *_ in unused_indexes:
                        self.stdout.write(f"  {schema}.{table}.{index}")

                # Database size
                cursor.execute(
                    "SELECT pg_size_pretty(pg_database_size(current_database()));"
                )
                db_size = cursor.fetchone()[0]
                self.stdout.write(f"\nTotal Database Size: {db_size}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Stats failed: {e}"))
