import sys
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from api.models import Product


class Command(BaseCommand):
    help = "Migrate and seed test product records for default, slave1, slave2, and slave3 databases"

    def handle(self, *args, **options):
        db_aliases = list(settings.DATABASES.keys())
        self.stdout.write(f"Setting up databases: {db_aliases}")

        for db in db_aliases:
            self.stdout.write(f"Running migrations for database '{db}'...")
            try:
                call_command("migrate", database=db, interactive=False)
                self.stdout.write(self.style.SUCCESS(f"Migrations complete for '{db}'"))
            except Exception as e:
                self.stderr.write(f"Error migrating database '{db}': {e}")
                continue

            # Seed product data if none exists
            count = Product.objects.using(db).count()
            if count == 0:
                self.stdout.write(f"Seeding sample products for database '{db}'...")
                Product.objects.using(db).create(
                    name=f"Product from {db.upper()}",
                    description=f"Sample product record residing in the {db} database",
                    price="29.99",
                    stock=100,
                )
                Product.objects.using(db).create(
                    name=f"Item {db.upper()}-002",
                    description=f"Secondary test item residing in {db}",
                    price="49.50",
                    stock=50,
                )
                self.stdout.write(self.style.SUCCESS(f"Seeded sample products in '{db}'"))
            else:
                self.stdout.write(f"Database '{db}' already contains {count} products.")

        self.stdout.write(self.style.SUCCESS("All databases setup successfully!"))
