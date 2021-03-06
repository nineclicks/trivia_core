import sys
import re
import requests
import html
import sqlite3
from time import sleep
REG_CATEGORIES = r'<table>[\S\s]*?<td class="(category_name|category_comments)">(.*?)<\/td>[\S\s]*?<td class="(category_name|category_comments)">(.*?)<\/td>[\S\s]*?<\/table>'
REG_QUESTIONS = r'(?:correct_response&quot;&gt;(.*?)&lt;\/em&gt;[\s\S]*?class="clue_text">(.*?)<\/td>|<td class="clue">\s*?<\/td>)'
REG_EPS = r'\"(https:\/\/www\.j-archive\.com\/showgame\.php\?game_id=\d+)\"'
REG_CAT_COMMENT = r'\(.+?:\s+(.*)\)'
REG_HTML_TAGS = r'<[^>]+>'
REG_SHOW_NUM = r'<div id=\"game_title\"><h1>Show #(\d+).*?(\d{4})<\/h1>'
REG_SEASON_NUMBERS = r'<a href=\"showseason\.php\?season=(\d+)\"'

SEASON_LIST_URL = 'https://j-archive.com/listseasons.php'
SEASON_URL = 'https://www.j-archive.com/showseason.php?season='

sample_page = 'https://www.j-archive.com/showgame.php?game_id=7094'

def build_tables(db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS category
               (id INTEGER NOT NULL PRIMARY KEY, show_number number, show_year number, title text, comment text)''')

    cur.execute('''CREATE TABLE IF NOT EXISTS question
               (id INTEGER NOT NULL PRIMARY KEY, category_id number, value number, question text, answer text, non_text number)''')

    con.commit()
    con.close()

def parse_page(url, db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cats = []
    questions = []
    scores = [200, 400, 600, 800, 1000]

    page_content = requests.get(url).content.decode('utf-8')
    show_info = re.search(REG_SHOW_NUM, page_content)
    show_num = int(show_info[1])
    show_year = int(show_info[2])

    cur.execute('''SELECT DISTINCT show_number FROM category WHERE show_number = ?''', (show_num, ))
    if len(cur.fetchall()) > 0:
        raise IndexError('Already have this episode')

    matches = re.findall(REG_CATEGORIES, page_content)[:12]
    if len(matches) != 12:
        con.rollback()
        raise IndexError('Wrong number of categories found! ' + str(len(matches)))

    # sometimes name and category are reversed
    for match in matches:
        if match[0] == 'category_name':
            category_name_index = 1
            category_comment_index = 3
        else:
            category_name_index = 3
            category_comment_index = 1

        category = clean_string(match[category_name_index])
        comment = clean_string(match[category_comment_index])

        # get rid of the (so-and-so: ) part of the comment
        com_match = re.match(REG_CAT_COMMENT, comment)
        if com_match:
            comment = com_match[1]

        if comment == '':
            comment = None

        cur.execute('''INSERT INTO category (show_number, show_year, title, comment) VALUES (?, ?, ?, ?)''', (show_num, show_year, category, comment))
        cats.append(cur.lastrowid)

    matches = re.findall(REG_QUESTIONS, page_content)
    if len(matches) != 60:
        con.rollback()
        raise IndexError('Wrong number of questions found!' + str(len(matches)))

    for i, match in enumerate(matches):
        if len(match) < 2 or match[0] == '' or match[1] == '':
            continue

        cat = cats[(i % 6) + 6 * (i // 30)]
        score = scores[(i % 30) // 6]
        question = match[1]
        non_text = 0
        if 'a href' in question.lower():
            non_text = 1
        question = clean_string(question)
        answer = clean_string(match[0])
        cur.execute('''INSERT INTO question (category_id, value, question, answer, non_text) VALUES (?, ?, ?, ?, ?)''', 
        (cat, score, question, answer, non_text))

    con.commit()
    con.close()

def clean_string(s):
    return re.sub(REG_HTML_TAGS, '', html.unescape(s)).replace('\\', '')

def scan_season(number, db_path):
    url = SEASON_URL + str(number)
    print(url)
    page_content = requests.get(url).content.decode('utf-8')
    matches = re.finditer(REG_EPS, page_content)

    for match in matches:
        ep_url = match[1]
        print(ep_url)
        try:
            parse_page(ep_url, db_path)
            sleep(.5)
        except Exception as ex:
            print(str(ex))

def get_seasons():
    page_content = requests.get(SEASON_LIST_URL).content.decode('utf-8')
    seasons = [int(s) for s in re.findall(REG_SEASON_NUMBERS, page_content)]
    return seasons

db_path = sys.argv[1]
commands = sys.argv[2:]

if len(commands) == 0:
    exit(
            'usage:\n'
            f'{sys.argv[0]} latest\n'
            f'{sys.argv[0]} season x\n'
            )

if commands[0] == 'latest':
    season = max(get_seasons())

elif commands[0].startswith('season'):
    if len(commands[0]) > len('season'):
        season = int(commands[0][len('season'):])

    else:
        season = int(commands[1])

build_tables(sys.argv[1])
scan_season(season, sys.argv[1])

