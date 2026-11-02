
from dataclasses import asdict
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

import csv
import argparse
import dataclasses
import logging
import re

from utils import JobInfo
from utils import clear_folder_os, load_config


logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Text Utilities
# ---------------------------------------------------------

def clean_text(text: str) -> str:
    """Normalize whitespace in a string."""

    if not text:
        return ""

    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------
# Job ID and URL
# ---------------------------------------------------------

def extract_job_id(card) -> str:
    """
    Extract Indeed job ID from the data-jk attribute.

    Example:
        data-jk="dec14bc190346cf6"
        -> "dec14bc190346cf6"
    """

    job_link = card.select_one("a.jcs-JobTitle[data-jk]")

    if not job_link:
        return ""

    return clean_text(job_link.get("data-jk", ""))


def extract_job_url(job_id: str) -> str:
    """
    Build a standard Indeed job URL from job ID.
    """

    if not job_id:
        return ""

    return f"https://ie.indeed.com/viewjob?jk={job_id}"


# ---------------------------------------------------------
# Job Title
# ---------------------------------------------------------

def extract_title(card) -> str:
    """
    Extract job title from Indeed job card.

    DOM structure:

        <h3 class="jobTitle">
            <a class="jcs-JobTitle" data-jk="...">
                <span title="Senior AI/ML Engineer">
                    Senior AI/ML Engineer
                </span>
            </a>
        </h3>
    """

    job_link = card.select_one("a.jcs-JobTitle")

    if not job_link:
        return ""

    title_span = job_link.find("span", title=True)

    if title_span:
        title = title_span.get("title", "")

        if title:
            return clean_text(title)

    return clean_text(job_link.get_text(" ", strip=True))


# ---------------------------------------------------------
# Company
# ---------------------------------------------------------

def extract_company(card) -> str:
    """
    Extract company name.

    Indeed selector:
        [data-testid="company-name"]
    """

    company = card.select_one(
        '[data-testid="company-name"]'
    )

    if company:
        return clean_text(
            company.get_text(" ", strip=True)
        )

    return ""


# ---------------------------------------------------------
# Location
# ---------------------------------------------------------

def extract_location(card) -> str:
    """
    Extract job location.

    Indeed selector:
        [data-testid="text-location"]
    """

    location = card.select_one(
        '[data-testid="text-location"]'
    )

    if location:
        return clean_text(
            location.get_text(" ", strip=True)
        )

    return ""


# ---------------------------------------------------------
# Salary
# ---------------------------------------------------------

def extract_salary(card) -> str:
    """
    Extract salary information from an Indeed job card.

    Supports examples:

        €70,000 - €90,000 a year
        €80,000 - €100,000 per year
        €50 - €70 an hour
        €500 - €600 a day
        €80K - €120K/yr

    Salary is not always available on the search results page.
    """

    salary_patterns = [

        # Currency range:
        # €70,000 - €90,000 a year
        r"[€$£]\s?[\d,.]+(?:K|k)?"
        r"\s?(?:-|–|to)\s?"
        r"[€$£]?\s?[\d,.]+(?:K|k)?"
        r"(?:\s?(?:a|per)\s?"
        r"(?:year|yr|month|hour|day|week))?",

        # Currency single value:
        # €80,000 a year
        r"[€$£]\s?[\d,.]+(?:K|k)?"
        r"(?:\s?(?:a|per)\s?"
        r"(?:year|yr|month|hour|day|week))?",

        # EUR range:
        # 70000 EUR - 90000 EUR
        r"[\d,.]+\s?(?:EUR|USD|GBP)"
        r"\s?(?:-|–|to)\s?"
        r"[\d,.]+\s?(?:EUR|USD|GBP)",

        # EUR single value:
        r"[\d,.]+\s?(?:EUR|USD|GBP)"
        r"(?:\s?(?:per|/)\s?"
        r"(?:year|yr|month|hour|day|week))?",
    ]

    # Search only inside the job card.
    # Avoid scanning the full page or job description.

    for element in card.select(
        ".salary-snippet-container, "
        ".metadata, "
        ".jobMetaDataGroup, "
        ".estimated-salary"
    ):

        text = clean_text(
            element.get_text(" ", strip=True)
        )

        for pattern in salary_patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:
                return match.group(0).strip()

    return ""


# ---------------------------------------------------------
# Posted Date
# ---------------------------------------------------------

def extract_posted_date(card) -> str:
    """
    Extract posted date from an Indeed job card.

    Supports examples:

        Posted 1 day ago
        Posted 3 days ago
        2 days ago
        Posted 1 week ago
        Just posted
        Employer reviewed job 2 days ago

    Returns empty string if unavailable.
    """

    patterns = [

        r"(?:Posted|Reposted)\s+"
        r"\d+\s+"
        r"(?:minute|minutes|hour|hours|day|days|"
        r"week|weeks|month|months)\s+ago",

        r"\d+\s+"
        r"(?:minute|minutes|hour|hours|day|days|"
        r"week|weeks|month|months)\s+ago",

        r"Just posted",

        r"Employer reviewed job\s+\d+\s+days?\s+ago",
    ]

    # Avoid scanning the entire page.
    # Date text may appear in different card elements.

    for element in card.find_all(
        ["span", "div", "time"]
    ):

        text = clean_text(
            element.get_text(" ", strip=True)
        )

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE
            )

            if match:
                return match.group(0)

    return ""

# ---------------------------------------------------------
# Job Type
# ---------------------------------------------------------

def extract_job_type(card) -> str:
    """
    Extract job type and work schedule.

    Indeed DOM:

        <li data-testid="attribute_snippet_testid">
            Full-time
        </li>

    Examples:
        Full-time
        Contract
        Part-time
        Full-time, Day shift
    """

    attributes = card.select(
        '[data-testid="attribute_snippet_testid"]'
    )

    job_types = []

    for attribute in attributes:

        text = clean_text(
            attribute.get_text(" ", strip=True)
        )

        if text and text not in job_types:
            job_types.append(text)

    return ", ".join(job_types)


# ---------------------------------------------------------
# Job Card Discovery
# ---------------------------------------------------------

def find_job_cards(soup):
    """
    Find Indeed job cards.

    Indeed search result structure:

        <table class="mainContentTable">
            <tr>
                <td class="resultContent">
                    ...
                    <a class="jcs-JobTitle"
                       data-jk="...">
                    ...
                </td>
            </tr>
        </table>

    Use the job title link as the anchor.
    Each link belongs to one job result row.

    This also avoids selecting the separate job detail pane.
    """

    job_links = soup.select(
        "a.jcs-JobTitle[data-jk]"
    )

    cards = []

    seen_ids = set()

    for job_link in job_links:

        job_id = clean_text(
            job_link.get("data-jk", "")
        )

        if not job_id or job_id in seen_ids:
            continue

        # Find the job result row.
        card = job_link.find_parent("tr")

        if not card:
            logger.warning(
                "Cannot find job card for job ID: %s",
                job_id
            )
            continue

        seen_ids.add(job_id)

        cards.append(card)

    return cards


# ---------------------------------------------------------
# Parse Indeed HTML
# ---------------------------------------------------------

def parse_jobs(html_path: str) -> list[JobInfo]:
    """
    Parse an Indeed HTML file and extract job records.

    Extracted fields:

        job_id
        title
        origin_company
        location
        salary
        posted_date
        job_url
        scrape_date

    """

    html_path = Path(html_path)

    with html_path.open(
        "r",
        encoding="utf-8"
    ) as f:

        html = f.read()

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    cards = find_job_cards(soup)

    logger.info(
        "Found %d Indeed job cards!",
        len(cards)
    )

    jobs = []

    for card in cards:

        job_id = extract_job_id(card)

        job_url = extract_job_url(job_id)

        title = extract_title(card)

        company = extract_company(card).strip().lower()

        location = extract_location(card)

        salary = extract_salary(card)

        posted_date = extract_posted_date(card)

        scrape_date = datetime.now().strftime("%Y%m%d")

        job = JobInfo(

            job_id=job_id,

            title=title,

            origin_company=company,

            location=location,

            salary=salary,

            posted_date=posted_date,

            job_url=job_url,

            scrape_date=scrape_date
        )

        jobs.append(job)

        logger.debug(
            "Parsed job: %s | %s | %s",
            title,
            company,
            job_id
        )

    return jobs


# ---------------------------------------------------------
# Save Jobs to CSV
# ---------------------------------------------------------

def save_jobs_to_csv(
    jobs: list[JobInfo],
    output_path: str
):
    """
    Save Indeed job records to CSV.

    Uses utf-8-sig encoding for Excel compatibility.
    """

    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    fieldnames = [
        f.name
        for f in dataclasses.fields(JobInfo)
    ]

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for job in jobs:

            writer.writerow(
                asdict(job)
            )

    logger.info(
        "Saved %d jobs successfully to %s",
        len(jobs),
        output_path.resolve()
    )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

if __name__ == "__main__":

    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    arg_parser = argparse.ArgumentParser(
        description="Parse Indeed HTML data"
    )

    arg_parser.add_argument(
        "--config",
        type=str,
        default=str(PROJECT_ROOT / "config.yaml"),
        help="Path to the YAML config file"
    )

    arg_parser.add_argument(
        "--start_page",
        type=int,
        default=1,
        help="Start page with pattern page_{page}.html"
    )

    arg_parser.add_argument(
        "--end_page",
        type=int,
        default=2,
        help="End page (included)"
    )

    arg_parser.add_argument(
        "--is_total",
        action="store_true",
        default=False,
        help="If all data (is_total=True), use filename; "
             "otherwise use filename_added"
    )

    args = arg_parser.parse_args()

    cfg = load_config(args.config)

    config_dir = Path(args.config).resolve().parent

    def resolve_path(base: Path, p) -> Path:

        p = Path(p)

        return p if p.is_absolute() else (base / p).resolve()

    html_data_dir = resolve_path(
        config_dir,
        cfg["indeed_html_data_dir"]
    )

    data_dir = resolve_path(
        config_dir,
        cfg["data_dir"]
    )

    file_pattern = "page_{page}.html"

    all_jobs = []

    for page in range(
        args.start_page,
        args.end_page + 1
    ):

        file_path = html_data_dir / file_pattern.format(
            page=page
        )

        if file_path.exists():

            logger.info(
                "Parsing Indeed page %d ...",
                page
            )

            jobs = parse_jobs(str(file_path))

            all_jobs.extend(jobs)

        else:

            logger.warning(
                "File not found: %s",
                file_path
            )

    if args.is_total:

        output_name = cfg["indeed_filename"]

    else:

        output_name = cfg["indeed_filename_added"]

    save_jobs_to_csv(
        all_jobs,
        data_dir / output_name
    )

    logger.info(
        "All Indeed jobs: %d saved locally!",
        len(all_jobs)
    )

    clear_folder_os(html_data_dir)
