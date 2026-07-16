EVAL_CASES = [
    {
        "query": "hiking sticks for bad knees",
        "expect_product_name_contains": "Trekking Poles",
        "expect_not_in_top_k": "Wireless Bluetooth Headphones",
        "note": "zero keyword overlap on the match; excluded item is an unrelated control",
    },
    {
        "query": "something to build arm muscle at home",
        "expect_product_name_contains": "Biceps Triceps Hexagonal Fixed Weight Dumbbell",
        "note": "paraphrase — no shared words with the product name at all",
    },
    {
        "query": "waterproof shoes for rainy weather",
        "expect_product_name_contains": "Quechua Arpenaz Novadry Boots",
        "note": "paraphrase — relies on 'Novadry' implying water resistance",
    },
    {
        "query": "help me sleep on a long flight",
        "expect_product_name_contains": "Bluetooth Sleeping Eye Mask",
        "note": "paraphrase — travel/sleep intent, no literal overlap",
    },
    {
        "query": "mug for hot coffee at my desk",
        "expect_product_name_contains": "Ceramic Mug",
        "expect_not_in_top_k": "Photo Frame",
        "note": "near-miss negative — shares 'ceramic'/Home & Kitchen with the excluded "
                "item, tests the system distinguishes drinkware from decor rather than "
                "just clustering on topic/material",
    },
    {
        "query": "coffee mug",
        "expect_min_result_count": 20,
        "note": "broad query, 166 real matches by keyword alone — floor/ceiling "
                "shouldn't artificially starve an abundant category",
    },
    {
        "query": "coffee",
        "expect_product_name_contains": "Coffee Beans",
        "note": "bare, unqualified single-word query, distinct from the more specific "
                "'coffee mug' and 'mug for hot coffee at my desk' cases above — a genuine "
                "coffee product is consistently the top match across repeated embedding "
                "calls, though coffee-branded merchandise (ceramic mugs) sometimes ranks "
                "close behind it, close enough that embedding noise alone can shuffle "
                "whether a given mug lands just inside or outside the confident set",
    },
    {
        "query": "car insurance policy",
        "expect_fallback": True,
        "note": "not a product at all — none of the 20 physical-goods categories "
                "should genuinely match; tests honest fallback instead of a forced match",
    },
    {
        "query": "laptop",
        "category_name": "Electronics",
        "expect_product_name_contains": "Laptop",
        "note": "confirms category filtering composes with ranking, not just standalone",
    },
    {
        "query": "instrument to learn guitar as a beginner",
        "expect_product_name_contains": "Fender CP-60S Acoustic Guitar",
        "note": "paraphrase — new category (Musical Instruments), no literal overlap",
    },
    {
        "query": "chew toy for my large dog",
        "expect_product_name_contains": "Dogzilla",
        "note": "paraphrase — new category (Pet Supplies), relies on 'Arctic Bone' framing implying a chew toy",
    },
    {
        "query": "tea to help me focus and stay energized",
        "expect_product_name_contains": "Indian Chai",
        "note": "paraphrase — new category (Grocery), product name explicitly says 'Energy, Focus' but zero query/name word overlap",
    },
    {
        "query": "rug for my patio floor",
        "expect_product_name_contains": "Coastal Tropical Carpet Outdoor Patio Rug",
        "expect_not_in_top_k": "Allure Auto",
        "note": "near-miss negative — car mats and a patio rug are both 'floor coverings', "
                "tests the system distinguishes home/outdoor decor from automotive parts",
    },
    {
        "query": "guitar",
        "expect_min_result_count": 20,
        "note": "broad query, 233 real matches by keyword alone — second abundance check in a different category than coffee mug",
    },
    {
        "query": "watch",
        "category_name": "Jewelry",
        "expect_product_name_contains": "Analog Watch",
        "note": "category filter composition check in a different category than laptop/Electronics",
    },
    {
        "query": "flight ticket to Paris",
        "expect_fallback": True,
        "note": "second genuine-absence case, a different kind of non-product (travel booking, not insurance) — tests the fallback pattern isn't a fluke specific to one query",
    },
]
