from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from catalog.models import Product, ClampSpecs
from catalog.services.clamp_parser import ClampParser
import time


class Command(BaseCommand):
    help = "Analiza y puebla las especificaciones técnicas (ClampSpecs) para todos los productos de tipo abrazadera."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Sobrescribe especificaciones incluso si ya existen (sin tocar las manual_override).",
        )

    def handle(self, *args, **options):
        force = options.get("force", False)
        start_time = time.time()

        self.stdout.write(self.style.NOTICE("Buscando productos de abrazaderas..."))

        products = Product.objects.filter(
            Q(name__icontains="ABRAZADERA")
            | Q(categories__name__icontains="ABRAZADERA")
            | Q(category__name__icontains="ABRAZADERA")
        ).distinct()

        total = products.count()
        self.stdout.write(self.style.NOTICE(f"Encontrados {total} productos relacionados con abrazaderas."))

        existing_specs = {s.product_id: s for s in ClampSpecs.objects.all()}
        to_create = []
        to_update = []

        for product in products.iterator(chunk_size=1000):
            specs_data = ClampParser.parse(product.description or product.name)

            if product.id in existing_specs:
                if force:
                    spec = existing_specs[product.id]
                    if not spec.manual_override:
                        spec.fabrication = specs_data.get("fabrication")
                        spec.diameter = specs_data.get("diameter")
                        spec.width = specs_data.get("width")
                        spec.length = specs_data.get("length")
                        spec.shape = specs_data.get("shape")
                        spec.parse_confidence = specs_data.get("parse_confidence", 0)
                        spec.parse_warnings = specs_data.get("parse_warnings", [])
                        to_update.append(spec)
            else:
                to_create.append(
                    ClampSpecs(
                        product=product,
                        fabrication=specs_data.get("fabrication"),
                        diameter=specs_data.get("diameter"),
                        width=specs_data.get("width"),
                        length=specs_data.get("length"),
                        shape=specs_data.get("shape"),
                        parse_confidence=specs_data.get("parse_confidence", 0),
                        parse_warnings=specs_data.get("parse_warnings", []),
                    )
                )

        with transaction.atomic():
            if to_create:
                self.stdout.write(f"Creando {len(to_create)} registros de especificaciones...")
                ClampSpecs.objects.bulk_create(to_create, batch_size=1000)

            if to_update:
                self.stdout.write(f"Actualizando {len(to_update)} registros de especificaciones...")
                ClampSpecs.objects.bulk_update(
                    to_update,
                    [
                        "fabrication",
                        "diameter",
                        "width",
                        "length",
                        "shape",
                        "parse_confidence",
                        "parse_warnings",
                    ],
                    batch_size=1000,
                )

        duration = time.time() - start_time
        self.stdout.write(
            self.style.SUCCESS(
                f"Proceso finalizado en {duration:.2f}s. Creados: {len(to_create)}, Actualizados: {len(to_update)}."
            )
        )
