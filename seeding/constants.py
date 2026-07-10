"""
Hand-authored data for the seed script. Kept separate from the generators
so the *content* (category list, price bands, name templates) is easy to
read and tune without touching any logic.
"""

# --- Identity / safety namespace -------------------------------------------

SEED_EMAIL_DOMAIN = "seed.smartkart.dev"
SEED_PASSWORD = "SeedData2026!Demo"  # shared by all bulk-random fake accounts; not a real secret

DEMO_EMAIL_DOMAIN = "smartkart.dev"
DEMO_PASSWORD = "SmartKartDemo2026!"  # documented, non-secret - for the known demo accounts only
DEMO_BUYER_EMAIL = f"demo.buyer@{DEMO_EMAIL_DOMAIN}"
DEMO_SELLER_EMAILS = [f"demo.seller1@{DEMO_EMAIL_DOMAIN}", f"demo.seller2@{DEMO_EMAIL_DOMAIN}"]

# --- Default scale (all overridable via CLI flags) --------------------------

DEFAULT_BUYERS = 5000
DEFAULT_SELLERS = 250
DEFAULT_PRODUCTS = 8000
DEFAULT_ORDERS = 15000

# --- Pexels image pool -------------------------------------------------------

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
PEXELS_IMAGES_PER_CATEGORY = 35
PEXELS_SEARCH_DELAY_SECONDS = 1.0     # between the ~20 quota-counted /search calls (safety margin)
PEXELS_DOWNLOAD_DELAY_SECONDS = 0.1   # between individual (unmetered) CDN downloads - courtesy, not quota
PEXELS_MAX_RETRIES = 3
PEXELS_RETRY_BACKOFF_SECONDS = 1.0    # doubles each retry: 1s, 2s, 4s

S3_SEED_IMAGE_PREFIX = "seed-images"
IMAGES_PER_PRODUCT_MIN = 1
IMAGES_PER_PRODUCT_MAX = 3

# --- Orders -------------------------------------------------------------

ORDER_MONTHS_BACK = 10
ORDER_STATUS_WEIGHTS = {"PLACED": 0.93, "CANCELLED": 0.07}
ORDER_ITEMS_MIN = 1
ORDER_ITEMS_MAX = 5
BUYER_ACTIVE_RATIO = 0.35  # ~35% of bulk buyers place at least one order

# --- Product name templates --------------------------------------------------

ADJECTIVES = [
    "Premium", "Classic", "Deluxe", "Compact", "Portable", "Professional",
    "Essential", "Ultra", "Everyday", "Signature", "Advanced", "Modern",
    "Rustic", "Elegant", "Heavy-Duty", "Lightweight", "Eco-Friendly",
    "Vintage", "Smart", "All-Purpose",
]

BRANDS = [
    "Nordic", "Zenith", "Apex", "Luma", "Vortex", "Cobalt", "Meridian",
    "Fern & Oak", "Highline", "Crestline", "Solace", "Nomad", "Anchor",
    "Quill", "Basecamp", "Ember", "Northstar", "Drift", "Harbor", "Pinecrest",
]

# Each entry: slug -> {name, price_min, price_max, price_mode, pexels_query, nouns}
CATEGORIES = {
    "electronics": {
        "name": "Electronics",
        "price": (15, 800, 60),
        "pexels_query": "electronics gadgets technology",
        "nouns": [
            "Bluetooth Headphones", "Wireless Earbuds", "Smartphone Charger",
            "USB-C Hub", "Portable Speaker", "Smartwatch", "Laptop Stand",
            "Webcam", "Mechanical Keyboard", "Wireless Mouse", "Power Bank",
            "Bluetooth Speaker", "Tablet Case", "HDMI Cable",
            "Noise-Cancelling Headphones",
        ],
    },
    "home-kitchen": {
        "name": "Home & Kitchen",
        "price": (10, 400, 40),
        "pexels_query": "kitchen home appliance",
        "nouns": [
            "Stainless Steel Cookware Set", "Air Fryer", "Coffee Maker",
            "Cutting Board", "Blender", "Knife Set", "Food Storage Containers",
            "Electric Kettle", "Toaster", "Dish Rack", "Cast Iron Skillet",
            "Kitchen Scale", "Spice Rack", "Baking Sheet Set", "Stand Mixer",
        ],
    },
    "fashion": {
        "name": "Fashion",
        "price": (12, 250, 35),
        "pexels_query": "fashion clothing apparel",
        "nouns": [
            "Cotton T-Shirt", "Denim Jacket", "Running Shoes", "Leather Wallet",
            "Wool Sweater", "Sunglasses", "Backpack", "Canvas Sneakers",
            "Baseball Cap", "Scarf", "Belt", "Hoodie", "Ankle Boots",
            "Crossbody Bag", "Rain Jacket",
        ],
    },
    "books": {
        "name": "Books",
        "price": (5, 60, 15),
        "pexels_query": "books reading library",
        "nouns": [
            "Mystery Novel", "Cookbook", "Self-Help Guide",
            "Fantasy Trilogy Box Set", "Biography", "Children's Picture Book",
            "Poetry Collection", "History Book", "Science Fiction Novel",
            "Travel Guide", "Graphic Novel", "Business Strategy Book",
            "Journal & Notebook Set", "Puzzle Book",
            "Language Learning Workbook",
        ],
    },
    "sports-outdoors": {
        "name": "Sports & Outdoors",
        "price": (8, 350, 40),
        "pexels_query": "sports outdoor fitness",
        "nouns": [
            "Yoga Mat", "Camping Tent", "Hiking Backpack", "Water Bottle",
            "Resistance Bands", "Sleeping Bag", "Bicycle Helmet",
            "Dumbbell Set", "Fishing Rod", "Camping Chair", "Running Belt",
            "Jump Rope", "Foam Roller", "Trekking Poles", "Insulated Cooler",
        ],
    },
    "beauty-personal-care": {
        "name": "Beauty & Personal Care",
        "price": (5, 150, 20),
        "pexels_query": "beauty cosmetics skincare",
        "nouns": [
            "Facial Cleanser", "Moisturizer", "Hair Dryer",
            "Electric Toothbrush", "Sunscreen SPF 50",
            "Shampoo & Conditioner Set", "Makeup Brush Set", "Nail Care Kit",
            "Beard Trimmer", "Face Mask Set", "Perfume", "Hair Straightener",
            "Lip Balm Set", "Body Lotion", "Skincare Serum",
        ],
    },
    "toys-games": {
        "name": "Toys & Games",
        "price": (6, 120, 25),
        "pexels_query": "toys kids games",
        "nouns": [
            "Building Block Set", "Board Game", "Remote Control Car",
            "1000-Piece Puzzle", "Action Figure", "Plush Toy", "Card Game",
            "Art & Craft Kit", "Educational Toy", "Model Kit",
            "Outdoor Play Tent", "Toy Kitchen Set", "Building Bricks Set",
            "Stuffed Animal", "Science Experiment Kit",
        ],
    },
    "grocery": {
        "name": "Grocery",
        "price": (3, 50, 12),
        "pexels_query": "grocery food pantry",
        "nouns": [
            "Organic Coffee Beans", "Extra Virgin Olive Oil",
            "Herbal Tea Sampler", "Granola Bars (12-Pack)", "Raw Honey",
            "Trail Mix", "Pasta Variety Pack", "Spice Blend Set",
            "Protein Powder", "Dark Chocolate Bar Set", "Rice (5lb Bag)",
            "Nut Butter", "Dried Fruit Mix", "Sparkling Water (Case)",
            "Cereal Variety Pack",
        ],
    },
    "automotive": {
        "name": "Automotive",
        "price": (8, 300, 35),
        "pexels_query": "car automotive accessories",
        "nouns": [
            "Car Phone Mount", "Dash Cam", "Tire Pressure Gauge",
            "Car Vacuum Cleaner", "Jump Starter Kit", "Car Seat Covers",
            "LED Headlight Bulbs", "Car Air Freshener Set", "Floor Mats",
            "Windshield Sun Shade", "OBD2 Scanner", "Car Wash Kit",
            "Steering Wheel Cover", "Trunk Organizer",
            "Emergency Roadside Kit",
        ],
    },
    "health-wellness": {
        "name": "Health & Wellness",
        "price": (8, 250, 30),
        "pexels_query": "health wellness fitness",
        "nouns": [
            "Digital Blood Pressure Monitor", "Vitamin D Supplements",
            "First Aid Kit", "Massage Gun", "Essential Oil Diffuser",
            "Compression Socks", "Posture Corrector", "Sleep Mask",
            "Fitness Tracker", "Heating Pad", "Multivitamin Bottle",
            "Probiotic Supplements", "Digital Thermometer",
            "Ankle Support Brace", "Weighted Blanket",
        ],
    },
    "pet-supplies": {
        "name": "Pet Supplies",
        "price": (5, 150, 25),
        "pexels_query": "pet dog cat supplies",
        "nouns": [
            "Dog Leash", "Cat Scratching Post", "Pet Food Bowl Set",
            "Dog Bed", "Cat Litter Box", "Pet Grooming Kit",
            "Dog Chew Toys", "Aquarium Filter", "Bird Cage", "Pet Carrier",
            "Dog Training Treats", "Cat Tree Tower", "Pet Water Fountain",
            "Dog Harness", "Small Animal Habitat",
        ],
    },
    "office-products": {
        "name": "Office Products",
        "price": (8, 500, 45),
        "pexels_query": "office desk supplies",
        "nouns": [
            "Ergonomic Office Chair", "Standing Desk Converter",
            "Desk Organizer Set", "Wireless Keyboard & Mouse Combo",
            "Monitor Stand", "Sticky Notes Pack", "Printer Paper (Ream)",
            "Desk Lamp", "Filing Cabinet", "Whiteboard",
            "Stapler & Supplies Set", "Laptop Bag", "Pen Set",
            "Cable Management Kit", "Bookshelf",
        ],
    },
    "furniture": {
        "name": "Furniture",
        "price": (40, 2000, 250),
        "pexels_query": "furniture home interior",
        "nouns": [
            "3-Seater Sofa", "Coffee Table", "Bookshelf", "Dining Table Set",
            "Bed Frame", "Accent Chair", "TV Stand", "Nightstand",
            "Bar Stool Set", "Wardrobe", "Recliner", "Console Table",
            "Bunk Bed", "Ottoman", "Bookcase",
        ],
    },
    "jewelry": {
        "name": "Jewelry",
        "price": (10, 5000, 80),
        "pexels_query": "jewelry rings necklace",
        "nouns": [
            "Sterling Silver Necklace", "Gold-Plated Hoop Earrings",
            "Diamond Stud Earrings", "Charm Bracelet", "Men's Watch",
            "Pearl Necklace", "Engagement Ring", "Cufflinks Set",
            "Pendant Necklace", "Tennis Bracelet", "Birthstone Ring",
            "Gemstone Earrings", "Chain Bracelet", "Locket Necklace",
            "Anklet",
        ],
    },
    "garden-outdoor": {
        "name": "Garden & Outdoor",
        "price": (10, 600, 60),
        "pexels_query": "garden outdoor patio",
        "nouns": [
            "Garden Hose", "Patio Umbrella", "Outdoor String Lights",
            "Gardening Tool Set", "Planter Pots (Set of 3)", "Lawn Mower",
            "Bird Feeder", "Outdoor Furniture Cover", "Garden Gnome",
            "Sprinkler System", "Wheelbarrow", "Fire Pit", "Hammock",
            "Patio Rug", "Solar Garden Lights",
        ],
    },
    "baby-products": {
        "name": "Baby Products",
        "price": (10, 350, 45),
        "pexels_query": "baby infant nursery",
        "nouns": [
            "Baby Stroller", "Diaper Bag", "Baby Monitor", "Car Seat",
            "Baby Bottle Set", "Crib Mattress", "Baby Carrier", "High Chair",
            "Baby Bath Tub", "Nursing Pillow", "Baby Blanket Set",
            "Pacifier Set", "Baby Play Mat", "Baby Swing", "Baby Food Maker",
        ],
    },
    "musical-instruments": {
        "name": "Musical Instruments",
        "price": (15, 1200, 100),
        "pexels_query": "musical instrument guitar",
        "nouns": [
            "Acoustic Guitar", "Electric Keyboard", "Ukulele",
            "Drum Practice Pad", "Violin", "Guitar Amplifier", "Microphone",
            "Guitar Strings (Pack)", "Digital Piano", "Harmonica",
            "Guitar Capo", "Music Stand", "Guitar Pick Set", "Bongo Drums",
            "Guitar Tuner",
        ],
    },
    "movies-tv": {
        "name": "Movies & TV",
        "price": (8, 90, 20),
        "pexels_query": "movies film entertainment",
        "nouns": [
            "Blu-ray Box Set", "4K UHD Movie Collection", "Classic Film Bundle",
            "TV Series Season Set", "Animated Movie Collection",
            "Documentary Collection", "Director's Cut Edition",
            "Anime Series Box Set", "Movie Franchise Bundle", "Vinyl Soundtrack",
        ],
    },
    "video-games": {
        "name": "Video Games",
        "price": (10, 400, 45),
        "pexels_query": "video game controller gaming",
        "nouns": [
            "Wireless Game Controller", "Gaming Headset",
            "Video Game (New Release)", "Gaming Mouse Pad",
            "Console Storage Case", "Gaming Chair", "VR Headset Accessory",
            "Game Capture Card", "Retro Game Console", "Gaming Keyboard",
        ],
    },
    "tools-home-improvement": {
        "name": "Tools & Home Improvement",
        "price": (8, 300, 35),
        "pexels_query": "tools hardware workshop",
        "nouns": [
            "Cordless Drill", "Tool Set (100-Piece)", "Adjustable Wrench Set",
            "Measuring Tape", "LED Work Light", "Hammer", "Screwdriver Set",
            "Tool Box", "Level Tool", "Utility Knife", "Paint Roller Set",
            "Extension Cord", "Safety Glasses", "Wire Strippers",
            "Socket Wrench Set",
        ],
    },
}
