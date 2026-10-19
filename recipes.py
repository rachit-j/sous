"""Fixed recipe definitions. Free-form recipes are out of scope for the MVP."""

SCRAMBLED_EGGS = {
    "id": "scrambled-eggs",
    "name": "Scrambled Eggs",
    "description": "Soft scrambled eggs for one, cooked gently in butter.",
    "ingredients": [
        "2 large eggs",
        "1 tablespoon butter",
        "Pinch of salt",
        "Pepper, to taste (optional)",
    ],
    "steps": [
        {
            "title": "Gather ingredients",
            "instruction": "Set out the eggs, butter, salt, a bowl, a fork or whisk, a nonstick pan and a spatula.",
            "visual_cue": "Eggs, butter, bowl, whisk or fork, pan and spatula are visible on the counter.",
        },
        {
            "title": "Crack and whisk",
            "instruction": "Crack the eggs into the bowl, add a pinch of salt and whisk until evenly yellow.",
            "visual_cue": "Bowl contains uniformly yellow beaten eggs with no visible separate whites.",
        },
        {
            "title": "Warm pan and add butter",
            "instruction": "Place the pan over medium-low heat and melt the butter, swirling to coat.",
            "visual_cue": "Pan is on the stove with melted, lightly foaming butter coating the surface.",
        },
        {
            "title": "Add eggs",
            "instruction": "Pour the beaten eggs into the buttered pan.",
            "visual_cue": "Liquid beaten eggs are in the pan and the bowl is empty or set aside.",
        },
        {
            "title": "Gently stir",
            "instruction": "Stir slowly with the spatula, pushing eggs from the edges, until soft curds form and they look just set.",
            "visual_cue": "Eggs have formed soft, moist curds with little or no runny liquid remaining.",
        },
        {
            "title": "Transfer to plate",
            "instruction": "Turn off the heat and move the eggs to a plate. Season with pepper if you like.",
            "visual_cue": "Scrambled eggs are on a plate and the pan is off the heat.",
        },
    ],
}

RECIPES = {SCRAMBLED_EGGS["id"]: SCRAMBLED_EGGS}
