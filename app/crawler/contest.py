import asyncio
import re
from typing import Dict, List

from beanie.odm.operators.update.general import Set
from loguru import logger

from app.crawler.utils import multi_http_request
from app.db.models import Contest
from app.utils import exception_logger_reraise


async def multi_upsert_contests(
    contests: List[Dict],
    past: bool,
) -> None:
    """
    Save meta data of Contests into MongoDB
    :param contests:
    :param past:
    :return:
    """
    tasks = list()
    for contest_dict in contests:
        try:
            contest_dict["past"] = past
            contest_dict["endTime"] = (
                contest_dict["startTime"] + contest_dict["duration"]
            )
            logger.debug(f"{contest_dict=}")
            contest = Contest.model_validate(contest_dict)
            logger.debug(f"{contest=}")
        except Exception as e:
            logger.exception(
                f"parse contest_dict error {e}. skip upsert {contest_dict=}"
            )
            continue
        tasks.append(
            Contest.find_one(Contest.titleSlug == contest.titleSlug,).upsert(
                Set(
                    {
                        Contest.update_time: contest.update_time,
                        Contest.title: contest.title,
                        Contest.startTime: contest.startTime,
                        Contest.duration: contest.duration,
                        Contest.past: past,
                        Contest.endTime: contest.endTime,
                    }
                ),
                on_insert=contest,
            )
        )
    await asyncio.gather(*tasks)
    logger.success("finished")


async def multi_request_past_contests(
    max_page_num: int,
) -> List[Dict]:
    """
    Fetch past contests information
    :param max_page_num:
    :return:
    """
    response_list = await multi_http_request(
        {
            page_num: {
                "url": "https://leetcode.com/graphql/",
                "method": "POST",
                "json": {
                    "query": """
                            query pastContests($pageNo: Int) {
                                pastContests(pageNo: $pageNo) {
                                    data { title titleSlug startTime duration }
                                }
                            }
                            """,
                    "variables": {"pageNo": page_num},
                },
            }
            for page_num in range(1, max_page_num + 1)
        },
        concurrent_num=10,
    )
    past_contests = list()
    for response in response_list:
        past_contests.extend(
            response.json().get("data", {}).get("pastContests", {}).get("data", [])
        )
    logger.success("finished")
    return past_contests


async def request_contest_homepage_text():
    req = (
        await multi_http_request(
            {
                "req": {
                    "url": "https://leetcode.com/contest/",
                    "method": "GET",
                }
            }
        )
    )[0]
    return req.text


async def save_past_contests() -> None:
    """
    Save past contests
    :return:
    """
    contest_page_text = await request_contest_homepage_text()
    max_page_num_search = re.search(
        re.compile(r'"pageNum":\s*(\d+)'),
        contest_page_text,
    )
    if not max_page_num_search:
        logger.error("cannot find pageNum")
        return
    max_page_num = int(max_page_num_search.groups()[0])
    past_contests = await multi_request_past_contests(max_page_num)
    await multi_upsert_contests(past_contests, past=True)
    logger.success("finished")


async def save_top_two_contests() -> None:
    """
    save two coming contests
    :return:
    """
    contest_page_text = await request_contest_homepage_text()
    build_id_search = re.search(
        re.compile(r'"buildId":\s*"(.*?)",'),
        contest_page_text,
    )
    if not build_id_search:
        logger.error("cannot find buildId")
        return
    build_id = build_id_search.groups()[0]
    next_data = (
        await multi_http_request(
            {
                "req": {
                    "url": f"https://leetcode.com/_next/data/{build_id}/contest.json",
                    "method": "GET",
                }
            }
        )
    )[0].json()
    top_two_contests = list()
    for queries in (
        next_data.get("pageProps", {}).get("dehydratedState", {}).get("queries", {})
    ):
        if "topTwoContests" in (data := queries.get("state", {}).get("data", {})):
            top_two_contests = data.get("topTwoContests")
            break
    if not top_two_contests:
        logger.error("cannot find topTwoContests")
        return
    logger.info(f"{top_two_contests=}")
    await multi_upsert_contests(top_two_contests, past=False)
    logger.success("finished")


@exception_logger_reraise
async def save_all_contests() -> None:
    """
    Save past contests and top two coming contests
    :return:
    """
    await save_top_two_contests()
    await save_past_contests()
    logger.success("finished")
