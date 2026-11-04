"""
Text Processing Utilities
==========================

Pure text manipulation functions with no business logic.
"""

import re


def truncate_words(text: str, max_words: int, suffix: str = "...") -> str:
    """
    Truncate text to maximum number of words.

    Args:
        text: Text to truncate
        max_words: Maximum number of words
        suffix: String to append if truncated

    Returns:
        Truncated text

    Example:
        >>> truncate_words("Hello world from Python", 2)
        'Hello world...'
    """
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + suffix


def slugify_unicode(text: str) -> str:
    """
    Slugify with Unicode support (Arabic, Chinese, Cyrillic, etc.).

    Args:
        text: Text to slugify

    Returns:
        URL-safe slug

    Example:
        >>> slugify_unicode("مرحبا بالعالم")
        'مرحبا-بالعالم'
    """
    from django.utils.text import slugify

    return slugify(text, allow_unicode=True)


def extract_urls(text: str) -> list[str]:
    """
    Extract all URLs from text.

    Args:
        text: Text to search

    Returns:
        List of URLs found

    Example:
        >>> extract_urls("Check https://example.com and http://test.com")
        ['https://example.com', 'http://test.com']
    """
    url_pattern = r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
    return re.findall(url_pattern, text)


def strip_html_tags(html: str) -> str:
    """
    Remove all HTML tags from string.

    Args:
        html: HTML string

    Returns:
        Plain text without HTML tags

    Example:
        >>> strip_html_tags("<p>Hello <b>world</b></p>")
        'Hello world'
    """
    return re.sub(r"<[^>]+>", "", html)


def word_count(text: str) -> int:
    """
    Count words in text.

    Args:
        text: Text to count

    Returns:
        Number of words

    Example:
        >>> word_count("Hello world from Python")
        4
    """
    return len(re.findall(r"\w+", text))


def reading_time(text: str, wpm: int = 200) -> int:
    """
    Calculate reading time in minutes.

    Args:
        text: Text to measure
        wpm: Words per minute (default: 200)

    Returns:
        Reading time in minutes (minimum 1)

    Example:
        >>> reading_time("Lorem ipsum " * 100)  # ~200 words
        1
    """
    words = word_count(text)
    return max(1, words // wpm)


def excerpt(text: str, max_length: int = 150, suffix: str = "...") -> str:
    """
    Create an excerpt from text.

    Args:
        text: Text to excerpt
        max_length: Maximum character length
        suffix: String to append if truncated

    Returns:
        Excerpted text

    Example:
        >>> excerpt("Lorem ipsum dolor sit amet", 10)
        'Lorem ipsum...'
    """
    if len(text) <= max_length:
        return text

    # Try to break at word boundary
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")

    if last_space > 0:
        truncated = truncated[:last_space]

    return truncated + suffix


def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text (remove extra spaces, newlines).

    Args:
        text: Text to normalize

    Returns:
        Text with normalized whitespace

    Example:
        >>> normalize_whitespace("Hello    world\\n\\nfrom  Python")
        'Hello world from Python'
    """
    # Replace multiple whitespace with single space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def title_case(text: str) -> str:
    """
    Convert text to title case (smart capitalization).

    Args:
        text: Text to convert

    Returns:
        Title-cased text

    Example:
        >>> title_case("the quick brown fox")
        'The Quick Brown Fox'
    """
    # Don't capitalize small words unless they're first/last
    small_words = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }

    words = text.split()
    result = []

    for i, word in enumerate(words):
        if i == 0 or i == len(words) - 1 or word.lower() not in small_words:
            result.append(word.capitalize())
        else:
            result.append(word.lower())

    return " ".join(result)


def remove_punctuation(text: str, keep: str = "") -> str:
    """
    Remove punctuation from text.

    Args:
        text: Text to process
        keep: Punctuation characters to keep

    Returns:
        Text without punctuation

    Example:
        >>> remove_punctuation("Hello, world!")
        'Hello world'
    """
    import string

    punctuation = "".join(c for c in string.punctuation if c not in keep)
    return text.translate(str.maketrans("", "", punctuation))


def contains_url(text: str) -> bool:
    """
    Check if text contains any URLs.

    Args:
        text: Text to check

    Returns:
        True if URLs found

    Example:
        >>> contains_url("Check https://example.com")
        True
    """
    return bool(extract_urls(text))


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename for safe filesystem usage.

    Args:
        filename: Filename to sanitize

    Returns:
        Safe filename

    Example:
        >>> sanitize_filename("my file?.txt")
        'my_file.txt'
    """
    # Remove/replace unsafe characters
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)
    filename = re.sub(r"\s+", "_", filename)
    return filename


__all__ = [
    "contains_url",
    "excerpt",
    "extract_urls",
    "normalize_whitespace",
    "reading_time",
    "remove_punctuation",
    "sanitize_filename",
    "slugify_unicode",
    "strip_html_tags",
    "title_case",
    "truncate_words",
    "word_count",
]
