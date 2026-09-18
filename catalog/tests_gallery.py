from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError

from catalog.models import Category, Product, ProductImage


class ProductGalleryTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            name="Grampas y Abrazaderas",
            slug="grampas-y-abrazaderas",
            is_active=True,
            visible_in_catalog=True,
        )
        self.product = Product.objects.create(
            sku="TEST-GALLERY-01",
            name="Abrazadera Galería Test",
            category=self.category,
            price=Decimal("1500.00"),
            stock=10,
            is_active=True,
            is_sellable=True,
        )

    def _create_sample_image(self, name="test.jpg"):
        # 1x1 white pixel JPEG bytes
        image_bytes = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
            b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4"
            b"\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        )
        return SimpleUploadedFile(name, image_bytes, content_type="image/jpeg")

    def test_gallery_syncs_primary_with_product_image(self):
        img1 = ProductImage.objects.create(
            product=self.product,
            image=self._create_sample_image("img1.jpg"),
            is_primary=True,
            order=0,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.image.name, img1.image.name)
        self.assertEqual(self.product.get_primary_image_url(), img1.image.url)

    def test_secondary_image_url(self):
        img1 = ProductImage.objects.create(
            product=self.product,
            image=self._create_sample_image("img1.jpg"),
            is_primary=True,
            order=0,
        )
        img2 = ProductImage.objects.create(
            product=self.product,
            image=self._create_sample_image("img2.jpg"),
            is_primary=False,
            order=1,
        )
        self.assertEqual(self.product.get_primary_image_url(), img1.image.url)
        self.assertEqual(self.product.get_secondary_image_url(), img2.image.url)

    def test_max_images_limit(self):
        for i in range(ProductImage.MAX_IMAGES_PER_PRODUCT):
            ProductImage.objects.create(
                product=self.product,
                image=self._create_sample_image(f"img_{i}.jpg"),
                order=i,
            )

        self.assertEqual(self.product.images.count(), 5)
        # Attempting to add a 6th image should trigger validation error on clean()
        sixth = ProductImage(
            product=self.product,
            image=self._create_sample_image("img_6.jpg"),
            order=5,
        )
        with self.assertRaises(ValidationError):
            sixth.clean()

    def test_product_detail_view_renders_gallery(self):
        img1 = ProductImage.objects.create(
            product=self.product,
            image=self._create_sample_image("img1.jpg"),
            is_primary=True,
            order=0,
        )
        img2 = ProductImage.objects.create(
            product=self.product,
            image=self._create_sample_image("img2.jpg"),
            is_primary=False,
            order=1,
        )

        client = Client()
        response = client.get(reverse("product_detail", kwargs={"sku": self.product.sku}))
        self.assertEqual(response.status_code, 200)
        self.assertIn("product_images", response.context)
        self.assertEqual(len(response.context["product_images"]), 2)
        self.assertContains(response, 'class="product-gallery-thumbs"')
        self.assertContains(response, 'class="gallery-thumb-btn')
        self.assertContains(response, 'id="galleryPrevBtn"')
        self.assertContains(response, 'id="galleryNextBtn"')
        self.assertContains(response, 'id="galleryCounter"')
