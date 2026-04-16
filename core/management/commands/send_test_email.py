from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a test email using the active Django email configuration."

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="Destination email address.")
        parser.add_argument(
            "--subject",
            default="FLEXS - prueba de email",
            help="Subject for the test email.",
        )

    def handle(self, *args, **options):
        recipient = str(options["recipient"]).strip()
        if not recipient or "@" not in recipient:
            raise CommandError("Indica un email destinatario valido.")

        backend = getattr(settings, "EMAIL_BACKEND", "")
        host = getattr(settings, "EMAIL_HOST", "")
        port = getattr(settings, "EMAIL_PORT", "")
        user = getattr(settings, "EMAIL_HOST_USER", "")
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
        self.stdout.write(f"EMAIL_BACKEND: {backend}")
        self.stdout.write(f"EMAIL_HOST: {host}:{port}")
        self.stdout.write(f"EMAIL_USE_TLS: {getattr(settings, 'EMAIL_USE_TLS', False)}")
        self.stdout.write(f"EMAIL_USE_SSL: {getattr(settings, 'EMAIL_USE_SSL', False)}")
        self.stdout.write(f"EMAIL_HOST_USER cargado: {'si' if user else 'no'}")
        self.stdout.write(f"DEFAULT_FROM_EMAIL: {from_email}")

        if "smtp" in str(backend).lower() and (not host or not user or not getattr(settings, "EMAIL_HOST_PASSWORD", "")):
            raise CommandError(
                "SMTP no esta completo. Revisa EMAIL_HOST, EMAIL_HOST_USER y EMAIL_HOST_PASSWORD en el .env."
            )

        sent = send_mail(
            subject=options["subject"],
            message=(
                "Este es un email de prueba de FLEXS.\n\n"
                "Si recibiste este mensaje, la configuracion SMTP del host funciona correctamente."
            ),
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
        if sent != 1:
            raise CommandError("Django no confirmo el envio del email.")

        self.stdout.write(self.style.SUCCESS(f"Email de prueba enviado a {recipient}."))
