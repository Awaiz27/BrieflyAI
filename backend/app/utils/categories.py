"""Category code to name mapping utilities."""

# Mapping of arXiv category codes to human-readable names
CATEGORY_MAPPING = {
    # Computer Science
    "cs.AI": "Artificial Intelligence",
    "cs.LG": "Machine Learning",
    "cs.CL": "Computation and Language",
    "cs.CV": "Computer Vision",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.RO": "Robotics",
    "cs.IR": "Information Retrieval",
    "cs.NLP": "Natural Language Processing",
    "cs.PL": "Programming Languages",
    "cs.SE": "Software Engineering",
    "cs.DB": "Databases",
    "cs.DC": "Distributed Computing",
    "cs.CR": "Cryptography and Security",
    # Statistics
    "stat.ML": "Machine Learning (Statistics)",
    "stat.AP": "Applications",
    "stat.ME": "Methodology",
    "stat.TH": "Theory",
    # Other common categories
    "math.ST": "Statistics",
    "math.NA": "Numerical Analysis",
    "q-bio.QM": "Quantitative Methods",
}


def get_category_name(category_code: str | None) -> str | None:
    """
    Convert arXiv category code to human-readable name.
    
    Args:
        category_code: arXiv category code (e.g., 'cs.AI')
        
    Returns:
        Human-readable category name or the original code if not in mapping
    """
    if not category_code:
        return None
    return CATEGORY_MAPPING.get(category_code, category_code)
