"""Deliberately chosen test inputs spanning the confidence range."""

TEST_INPUTS = {
    "clear_ai": (
        "Artificial intelligence represents a transformative paradigm shift in modern society. "
        "It is important to note that while the benefits of AI are numerous, it is equally "
        "essential to consider the ethical implications. Furthermore, stakeholders across "
        "various sectors must collaborate to ensure responsible deployment."
    ),
    "clear_human": (
        "ok so i finally tried that new ramen place downtown and honestly? "
        "underwhelming. the broth was fine but they put WAY too much sodium in it and "
        "i was thirsty for like three hours after. my friend got the spicy version and "
        "said it was better. probably won't go back unless someone drags me there"
    ),
    "borderline_formal_human": (
        "The relationship between monetary policy and asset price inflation has been "
        "extensively studied in the literature. Central banks face a fundamental tension "
        "between their mandate for price stability and the unintended consequences of "
        "prolonged low interest rates on equity and real estate valuations."
    ),
    "borderline_edited_ai": (
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs — "
        "flexibility and no commute on one side, isolation and blurred work-life boundaries "
        "on the other. Studies show productivity varies widely by individual and role type."
    ),
}

# Edge cases from planning.md: human-written, but structurally "uniform".
TEST_INPUTS["edge_repetitive_poem"] = (
    "I will not go into the dark tonight.\n"
    "I will not go where the cold wind blows.\n"
    "I will not go though the stars are bright.\n"
    "I will not go where the river flows.\n"
    "My mother waits by the kitchen light.\n"
    "My mother waits and the kettle glows.\n"
    "I will not go into the dark tonight."
)
TEST_INPUTS["edge_non_native_formal"] = (
    "I arrived to this city three years ago for my studies. In the beginning, the life here was "
    "very difficult for me because I did not understand the customs of the people. The weather "
    "was also very cold compared to my country. However, I have made many good friends and now "
    "I feel this place is my second home. I am grateful for every experience I have received here."
)

# A second, longer AI sample (typical assistant-style blog prose) so calibration isn't tuned to one text.
TEST_INPUTS["clear_ai_long"] = (
    "In today's fast-paced digital landscape, effective time management has become more important "
    "than ever. By prioritizing tasks, setting clear goals, and minimizing distractions, individuals "
    "can significantly enhance their productivity. Additionally, leveraging tools such as digital "
    "calendars and task management applications can help streamline daily workflows. Ultimately, "
    "mastering time management is a valuable skill that empowers individuals to achieve a healthier "
    "work-life balance and reach their full potential."
)
