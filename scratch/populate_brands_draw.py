import os
import sys
import django
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Set up Django environment
sys.path.append('c:\\Users\\Brian\\Desktop\\webflexs')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.local')
django.setup()

from catalog.models import Brand

# Output directory for logos
media_dir = Path('c:\\Users\\Brian\\Desktop\\webflexs\\media\\brands\\logos')
media_dir.mkdir(parents=True, exist_ok=True)

def create_logo(brand_name):
    # Canvas is 1200x1200 for high resolution, resized later to 400x400
    size = 1200
    img = Image.new('RGB', (size, size), '#FFFFFF')
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    
    font_path = "C:\\Windows\\Fonts\\arialbd.ttf"
    font_italic_path = "C:\\Windows\\Fonts\\arialbi.ttf"
    
    # We will customize drawing for each brand
    name_lower = brand_name.lower()
    
    if "ford" in name_lower:
        # Ford Oval
        # Outer ellipse
        draw.ellipse([cx - 500, cy - 300, cx + 500, cy + 300], fill='#0D2C54')
        # Silver border 1
        draw.ellipse([cx - 480, cy - 280, cx + 480, cy + 280], outline='#CCCCCC', width=20)
        # Silver border 2
        draw.ellipse([cx - 450, cy - 250, cx + 450, cy + 250], outline='#FFFFFF', width=10)
        # Text "Ford" slanted
        try:
            font = ImageFont.truetype(font_italic_path, 260)
        except:
            font = ImageFont.load_default()
        # Draw text at center
        text = "Ford"
        if hasattr(font, 'getbbox'):
            bbox = font.getbbox(text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        elif hasattr(font, 'getsize'):
            w, h = font.getsize(text)
        else:
            w, h = 500, 200
        draw.text((cx - w//2 - 20, cy - h//2 - 40), text, fill='#FFFFFF', font=font)
        
    elif "mercedes" in name_lower:
        # Mercedes three-pointed star
        # Silver outer circle
        draw.ellipse([cx - 400, cy - 400, cx + 400, cy + 400], outline='#B0B0B0', width=25)
        # Inner circle
        draw.ellipse([cx - 380, cy - 380, cx + 380, cy + 380], outline='#E0E0E0', width=8)
        
        # Star vertices
        # Tip points (radius 380)
        T = (cx, cy - 385)
        R = (cx + 333, cy + 192)
        L = (cx - 333, cy + 192)
        C = (cx, cy)
        
        # Valley points (radius 60)
        V_tr = (cx + 52, cy - 30)
        V_b = (cx, cy + 60)
        V_tl = (cx - 52, cy - 30)
        
        # Draw 6 triangles for 3D metallic effect
        draw.polygon([C, T, V_tl], fill='#E1E1E1')
        draw.polygon([C, T, V_tr], fill='#999999')
        draw.polygon([C, R, V_tr], fill='#F0F0F0')
        draw.polygon([C, R, V_b], fill='#B3B3B3')
        draw.polygon([C, L, V_b], fill='#CCCCCC')
        draw.polygon([C, L, V_tl], fill='#7F7F7F')
        
    elif "fiat" in name_lower:
        # Fiat red badge with silver letters
        # Red rounded rect
        draw.rounded_rectangle([cx - 350, cy - 350, cx + 350, cy + 350], radius=80, fill='#9E1B22')
        # Silver border
        draw.rounded_rectangle([cx - 350, cy - 350, cx + 350, cy + 350], radius=80, outline='#D0D0D0', width=25)
        
        # Draw letters F, I, A, T manually with lines for perfect custom look
        # Vertical stroke width
        sw = 45
        # Letter color
        lc = '#FFFFFF'
        
        # F: at left
        fx = cx - 220
        draw.rectangle([fx, cy - 180, fx + sw, cy + 180], fill=lc) # vertical
        draw.rectangle([fx, cy - 180, fx + 120, cy - 180 + sw], fill=lc) # top horiz
        draw.rectangle([fx, cy - 20, fx + 90, cy - 20 + sw], fill=lc) # mid horiz
        
        # I: middle-left
        ix = cx - 50
        draw.rectangle([ix, cy - 180, ix + sw, cy + 180], fill=lc)
        
        # A: middle-right
        ax = cx + 50
        # draw slanted strokes for A
        draw.polygon([(ax - 10, cy + 180), (ax + 35, cy - 180), (ax + 35 + sw, cy - 180), (ax + sw - 10, cy + 180)], fill=lc)
        draw.polygon([(ax + 110, cy + 180), (ax + 65, cy - 180), (ax + 65 - sw, cy - 180), (ax + 110 - sw, cy + 180)], fill=lc)
        # horiz bar
        draw.rectangle([ax + 15, cy + 30, ax + 85, cy + 30 + sw//2], fill=lc)
        
        # T: right
        tx = cx + 180
        draw.rectangle([tx + 50, cy - 180, tx + 50 + sw, cy + 180], fill=lc) # vert
        draw.rectangle([tx, cy - 180, tx + 140, cy - 180 + sw], fill=lc) # top
        
    elif "scania" in name_lower:
        # Scania blue badge with crowned red griffin
        draw.ellipse([cx - 450, cy - 450, cx + 450, cy + 450], fill='#002C6C')
        draw.ellipse([cx - 450, cy - 450, cx + 450, cy + 450], outline='#CCCCCC', width=25)
        
        # Crowned Griffin silhouette (stylized red crown)
        draw.polygon([
            (cx - 120, cy + 40), (cx - 100, cy - 120), (cx - 40, cy - 50),
            (cx, cy - 160), (cx + 40, cy - 50), (cx + 100, cy - 120), (cx + 120, cy + 40)
        ], fill='#E11C24')
        # Bottom of crown
        draw.rectangle([cx - 120, cy + 40, cx + 120, cy + 100], fill='#E11C24')
        # Gold crown highlights
        draw.rectangle([cx - 100, cy + 50, cx + 100, cy + 70], fill='#F1B82D')
        
        # SCANIA Text at bottom curve/center
        try:
            font = ImageFont.truetype(font_path, 110)
        except:
            font = ImageFont.load_default()
        text = "SCANIA"
        if hasattr(font, 'getbbox'):
            bbox = font.getbbox(text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        elif hasattr(font, 'getsize'):
            w, h = font.getsize(text)
        else:
            w, h = 400, 100
        draw.text((cx - w//2, cy + 180), text, fill='#FFFFFF', font=font)
        
    elif "volvo" in name_lower:
        # Volvo Iron Mark
        # Silver Circle
        draw.ellipse([cx - 320, cy - 320, cx + 320, cy + 320], outline='#CCCCCC', width=35)
        
        # Diagonal Arrow (45 degrees)
        draw.line([cx + 200, cy - 200, cx + 380, cy - 380], fill='#CCCCCC', width=35)
        # Arrow head (triangle pointing up-right)
        draw.polygon([(cx + 330, cy - 430), (cx + 430, cy - 330), (cx + 430, cy - 430)], fill='#CCCCCC')
        
        # Blue bar across center
        draw.rectangle([cx - 360, cy - 70, cx + 360, cy + 70], fill='#001F60')
        draw.rectangle([cx - 360, cy - 70, cx + 360, cy + 70], outline='#FFFFFF', width=4)
        
        # Text "VOLVO" in white
        try:
            font = ImageFont.truetype(font_path, 110)
        except:
            font = ImageFont.load_default()
        text = "VOLVO"
        if hasattr(font, 'getbbox'):
            bbox = font.getbbox(text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        elif hasattr(font, 'getsize'):
            w, h = font.getsize(text)
        else:
            w, h = 300, 100
        draw.text((cx - w//2, cy - h//2 - 10), text, fill='#FFFFFF', font=font)
        
    elif "chevrolet" in name_lower:
        # Chevrolet Gold Bowtie
        points = [
            (cx - 450, cy),
            (cx - 200, cy - 120),
            (cx - 200, cy - 240),
            (cx + 200, cy - 240),
            (cx + 200, cy - 120),
            (cx + 450, cy),
            (cx + 200, cy + 120),
            (cx + 200, cy + 240),
            (cx - 200, cy + 240),
            (cx - 200, cy + 120)
        ]
        # Draw Gold filled polygon
        draw.polygon(points, fill='#E0A900')
        # Silver border
        draw.polygon(points, outline='#9E7600', width=18)
        draw.polygon(points, outline='#FFFFFF', width=6)
        
    elif "volkswagen" in name_lower:
        # VW Logo
        draw.ellipse([cx - 400, cy - 400, cx + 400, cy + 400], fill='#001C54')
        draw.ellipse([cx - 400, cy - 400, cx + 400, cy + 400], outline='#CCCCCC', width=30)
        
        # Draw V and W white strokes (width 35)
        draw.line([(cx - 190, cy - 250), (cx, cy - 20)], fill='#FFFFFF', width=35)
        draw.line([(cx + 190, cy - 250), (cx, cy - 20)], fill='#FFFFFF', width=35)
        
        draw.line([(cx - 215, cy - 10), (cx - 110, cy + 260)], fill='#FFFFFF', width=35)
        draw.line([(cx - 110, cy + 260), (cx, cy - 10)], fill='#FFFFFF', width=35)
        draw.line([(cx, cy - 10), (cx + 110, cy + 260)], fill='#FFFFFF', width=35)
        draw.line([(cx + 110, cy + 260), (cx + 215, cy - 10)], fill='#FFFFFF', width=35)
        
    elif "toyota" in name_lower:
        # Toyota logo: Red emblem
        # Outer ellipse
        draw.ellipse([cx - 420, cy - 280, cx + 420, cy + 280], outline='#EB0A1E', width=45)
        # Vertical ellipse
        draw.ellipse([cx - 140, cy - 250, cx + 140, cy + 250], outline='#EB0A1E', width=35)
        # Horizontal ellipse (higher)
        draw.ellipse([cx - 280, cy - 160, cx + 280, cy + 90], outline='#EB0A1E', width=35)
        
    elif "iveco" in name_lower:
        # Iveco bold lettering
        try:
            font = ImageFont.truetype(font_path, 280)
        except:
            font = ImageFont.load_default()
        text = "IVECO"
        if hasattr(font, 'getbbox'):
            bbox = font.getbbox(text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        elif hasattr(font, 'getsize'):
            w, h = font.getsize(text)
        else:
            w, h = 600, 200
        draw.text((cx - w//2, cy - h//2 - 20), text, fill='#002C6C', font=font)
        
    elif "dodge" in name_lower:
        # Dodge text + two red diagonal stripes
        try:
            font = ImageFont.truetype(font_italic_path, 210)
        except:
            font = ImageFont.load_default()
        text = "DODGE"
        if hasattr(font, 'getbbox'):
            bbox = font.getbbox(text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        elif hasattr(font, 'getsize'):
            w, h = font.getsize(text)
        else:
            w, h = 500, 150
        
        draw.text((cx - w//2 - 80, cy - h//2 - 20), text, fill='#111111', font=font)
        
        # Two red stripes next to it
        # Stripe 1
        draw.polygon([
            (cx + 260, cy - 90), (cx + 310, cy - 90),
            (cx + 250, cy + 90), (cx + 200, cy + 90)
        ], fill='#E81A23')
        # Stripe 2
        draw.polygon([
            (cx + 330, cy - 90), (cx + 380, cy - 90),
            (cx + 320, cy + 90), (cx + 270, cy + 90)
        ], fill='#E81A23')
        
    elif "agrale" in name_lower:
        # Agrale robust font in forest green with red slash
        try:
            font = ImageFont.truetype(font_path, 220)
        except:
            font = ImageFont.load_default()
        text = "AGRALE"
        if hasattr(font, 'getbbox'):
            bbox = font.getbbox(text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        elif hasattr(font, 'getsize'):
            w, h = font.getsize(text)
        else:
            w, h = 600, 150
        draw.text((cx - w//2, cy - h//2 - 60), text, fill='#007A33', font=font)
        
        # Draw red accent bar underneath
        draw.rectangle([cx - w//2, cy + h//2 - 20, cx + w//2, cy + h//2], fill='#E11C24')
        
    elif "peugeot" in name_lower:
        # Peugeot modern shield with white lion outline
        shield_points = [
            (cx - 300, cy - 350),
            (cx + 300, cy - 350),
            (cx + 300, cy + 120),
            (cx, cy + 390),
            (cx - 300, cy + 120)
        ]
        draw.polygon(shield_points, fill='#111111')
        draw.polygon(shield_points, outline='#E0E0E0', width=18)
        
        # Stylized lion head points (drawn as simple sharp polygons for a modern geometric lion head)
        draw.polygon([(cx - 150, cy - 100), (cx - 100, cy - 200), (cx, cy - 220), (cx + 50, cy - 180), (cx + 100, cy - 100), (cx - 50, cy - 50)], fill='#FFFFFF')
        draw.polygon([(cx + 100, cy - 100), (cx + 180, cy - 80), (cx + 160, cy - 20), (cx + 80, cy - 40)], fill='#FFFFFF')
        draw.polygon([(cx + 80, cy - 40), (cx + 120, cy + 80), (cx, cy + 140), (cx - 100, cy + 100), (cx - 50, cy - 50)], fill='#FFFFFF')
        
        # Peugeot word on top
        try:
            font = ImageFont.truetype(font_path, 45)
        except:
            font = ImageFont.load_default()
        text = "PEUGEOT"
        if hasattr(font, 'getbbox'):
            bbox = font.getbbox(text)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        elif hasattr(font, 'getsize'):
            tw, th = font.getsize(text)
        else:
            tw, th = 200, 40
        draw.text((cx - tw//2, cy - 310), text, fill='#FFFFFF', font=font)
        
    elif "renault" in name_lower:
        # Renault Diamond with 3D metallic facets
        draw.polygon([
            (cx - 20, cy - 380), (cx - 240, cy), (cx - 20, cy + 380),
            (cx - 20, cy + 220), (cx - 120, cy), (cx - 20, cy - 220)
        ], fill='#EAEAEA')
        draw.polygon([
            (cx + 20, cy - 380), (cx + 240, cy), (cx + 20, cy + 380),
            (cx + 20, cy + 220), (cx + 120, cy), (cx + 20, cy - 220)
        ], fill='#9E9E9E')
        
    else:
        # Default fallback logo - nice badge with initial letter
        draw.ellipse([cx - 400, cy - 400, cx + 400, cy + 400], fill='#FF6B35')
        try:
            font = ImageFont.truetype(font_path, 400)
        except:
            font = ImageFont.load_default()
        text = brand_name[0].upper()
        if hasattr(font, 'getbbox'):
            bbox = font.getbbox(text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        elif hasattr(font, 'getsize'):
            w, h = font.getsize(text)
        else:
            w, h = 300, 300
        draw.text((cx - w//2, cy - h//2 - 50), text, fill='#FFFFFF', font=font)

    # Resize with antialiasing (LANCZOS)
    img_resized = img.resize((400, 400), Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.ANTIALIAS)
    
    slug = brand_name.lower().replace(" ", "-")
    logo_filename = f"{slug}.png"
    logo_path = media_dir / logo_filename
    img_resized.save(logo_path, format='PNG')
    print(f"Generated logo for {brand_name} saved to {logo_path}")
    return f"brands/logos/{logo_filename}"

# List of all brands in our catalog
BRANDS = [
    "Ford", "Mercedes-Benz", "Fiat", "Scania", "Volvo", "Chevrolet",
    "Volkswagen", "Toyota", "Iveco", "Dodge", "Agrale", "Peugeot", "Renault"
]

print("Starting generation of brand logo images...")
for brand_name in BRANDS:
    db_logo_path = create_logo(brand_name)
    
    # Update Brand in DB
    brand, created = Brand.objects.get_or_create(
        name=brand_name,
        defaults={
            "is_active": True,
            "order": 10
        }
    )
    brand.logo = db_logo_path
    brand.save()
    print(f"Updated DB for Brand '{brand_name}' with logo: {brand.logo}")

print("Brand import and logo generation complete!")
