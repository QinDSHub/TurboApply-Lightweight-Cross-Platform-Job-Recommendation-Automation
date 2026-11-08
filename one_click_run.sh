#!/bin/bash

linkedin_start_page=1
linkedin_end_page=1

indeed_start_page=1
indeed_end_page=1

irishjobs_start_page=1
irishjobs_end_page=1

# run monthly report/online metric at the start of the month
run_monthly_report="True"

# when you have new interview company, you could update it as "True"
open_similar_company_recommend="False"

config="$(pwd)/config.yaml"


RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}🚀 Starting parsing and recommending${NC}"

if (
    cd src || { echo -e "${RED}❌ no src path${NC}"; exit 1; }

    echo -e "${GREEN}[1/3] Parse LinkedIn Data...${NC}"
    poetry run python parser_linkedin.py --start_page $linkedin_start_page --end_page $linkedin_end_page --config $config || exit 1

    echo -e "${GREEN}[2/3] Parse Indeed Data...${NC}"
    poetry run python parser_indeed.py --start_page $indeed_start_page --end_page $indeed_end_page --config $config || exit 1

    echo -e "${GREEN}[3/3] Parse IrishJobs Data...${NC}"
    poetry run python parser_irishjobs.py --start_page $irishjobs_start_page --end_page $irishjobs_end_page --config $config || exit 1

    if [ "$open_similar_company_recommend" = "True" ]; then
        echo -e "Starting chain agents to recommend based on your interview companies"
        poetry run python company_recommend.py --config "$config" || exit 1
    else
        echo "Skipping similar company recommendation"
    fi

    echo -e "${GREEN}[3/3] Generate recommendation...${NC}"
    poetry run python recommend.py --config $config || exit 1

    if [ "$run_monthly_report" = "True" ]; then
        echo -e "${GREEN} Run monthly report...${NC}"
        poetry run python monitor.py --config $config || exit 1
    else:
        echo "Run monthly online metric report at the start of the month, not now!"
    fi
); then
    echo -e "${GREEN}✅ All tasks finished successfully! ${NC}"
else
    echo -e "${RED}❌ Tasks execution failed! ${NC}"
    exit 1
fi