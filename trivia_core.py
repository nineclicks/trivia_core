"""
Module for TriviaCore class
"""

import os
import re
import time
import signal
import logging
import datetime
from threading import Lock
from typing import Callable

import unidecode
from tabulate import tabulate
from num2words import num2words
from apscheduler.schedulers.background import BackgroundScheduler

from trivia_database import TriviaDatabase

class TriviaCore:
    """
    Core trivia components
    """
    # pylint: disable=too-many-instance-attributes

    def __init__(self, database_path, **kwargs):
        logging.info('Starting Trivia Core')
        self._lock = Lock()
        self._starttime = time.time()
        self._admin_uid = kwargs.get('admin_uid')
        self._attempts = []
        self._post_question_handler = lambda x: None
        self._post_message_handler = lambda x: None
        self._post_reply_handler = lambda x: None
        self._pre_format_handler = lambda x: x
        self._get_display_name_handler = lambda x: x
        self._min_matching_characters = kwargs.get('min_matching_characters', 5)
        self._platform = kwargs.get('platform')
        self._db = TriviaDatabase(database_path)
        self._command_prefix = '!'
        self._current_question = self._get_last_question()
        self._create_scoreboard_schedule(kwargs['scoreboard_schedule'])

    def _create_scoreboard_schedule(self, schedules):
        self._sched = BackgroundScheduler()
        self._sched.start()  

        for schedule in schedules:
            self._job = self._sched.add_job(
                self._show_scores,
                'cron',
                **schedule['time'],
                kwargs={'days_ago': schedule['days_ago'], 'suppress_no_scores': True},
                replace_existing=False)


    def handle_message(self, uid:str, text:str, message_payload, correct_callback:callable):
        """
        Handle incoming answers and commands from users
        """

        with self._lock:
            if text.startswith(self._command_prefix):
                self._handle_command(uid, text[1:], message_payload)

            else:
                self._attempt_answer(uid, text, correct_callback)

    def on_pre_format(self, func):
        """Decorate you preformatted text handler function.
        """
        self._pre_format_handler = func

        return func

    def on_post_question(self, func):
        """Decorate you post question handler function.
        """
        self._post_question_handler = func

        if self._current_question is None:
            self._new_question()

        return func

    def on_post_message(self, func):
        """Decorate you post message handler function.
        """
        self._post_message_handler = func

        return func

    def on_post_reply(self, func):
        """Decorate you post reply handler function.
        """
        self._post_reply_handler = func

        return func

    def on_get_display_name(self, func):
        """Decorate you get display name handler function.
        """
        self._get_display_name_handler = func

        return func

    def _get_new_question(self):
        """
        Select a random question from the database
        """

        q = self._db.select_one('get_random_question', as_map=True)
        return q

    def _get_last_question(self):
        """
        Restore the last unanswered question
        """

        return self._db.select_one('get_last_question', as_map=True)

    def _new_question(self, winning_user=None):
        if self._current_question:
            winning_answer = self._current_question['answer']
        else:
            winning_answer = None

        self._attempts = []
        question = self._create_question_round()
        self._post_question_handler({
            'winning_user': winning_user,
            'winning_answer': winning_answer,
            **question
            })

    def _attempt_answer(self, uid:str, answer:str, correct_callback:Callable=None):
        self._attempts.append(uid)

        if self._check_answer(answer):
            if correct_callback:
                correct_callback()

            self._complete_question_round(winning_uid=uid)

    def _handle_command(self, uid, text, message_payload):
        for command in self._commands():
            if text in command[0]:
                command[2](uid=uid, text=text, message_payload=message_payload)
                break

    def _check_answer(self, answer):
        """
        Check an answer against the current question
        """

        correct_answer = self._current_question['answer']
        return self._do_check_answer(answer, correct_answer, self._min_matching_characters)
    def _player_attempt(self, uid, attempts, correct):
        self._db.execute('player_attempt', {
            'uid': uid,
            'attempts': int(attempts),
            'correct': int(correct)
            }, auto_commit=True)

    def _complete_question_round(self, winning_uid):
        for attempt_user in set(self._attempts):
            self._add_user(attempt_user)
            self._player_attempt(
                    attempt_user,
                    self._attempts.count(attempt_user),
                    attempt_user == winning_uid
                    )

        self._update_question_round_table(
            correct_uid=winning_uid,
        )

        winning_user = None

        if winning_uid:
            stats = self._get_player_stats_timeframe(winning_uid, self._timestamp_midnight())
            winning_user = next(stats, None) # TODO deal with None case
            stats = None # This is crutial to release the generator and therefore the db lock

        self._new_question(winning_user)

    def _commands(self):
        return (
            (
                ['exit'],
                None,
                self._command_exit
            ),
            (
                ['uptime'],
                None,
                self._command_uptime
            ),
            (
                ['new', 'trivia new'],
                'Skip to the next question',
                lambda *_, **__: self._complete_question_round(winning_uid=None)
            ),
            (
                ['alltime', 'score', 'scores'],
                'Scores for all time',
                lambda *_, **__: self._show_scores(days_ago=None, suppress_no_scores=False)
            ),
            (
                ['yesterday'],
                'Scores for yesterday',
                lambda *_, **__: self._show_scores(days_ago=1, suppress_no_scores=False)
            ),
            (
                ['today'],
                'Scores for today',
                lambda *_, **__: self._show_scores(days_ago=0, suppress_no_scores=False)
            ),
            (
                ['help'],
                'Show this help info',
                self._command_help
            ),
        )

    def _add_user(self, uid):
        self._db.execute('add_player', {
            'uid': uid,
            'platform': self._platform
        }, auto_commit=True)

    def _user_wrong_answer(self, uid):
        self._db.execute('answer_wrong', {
            'uid': uid,
            'platform': self._platform,
        }, auto_commit=True)

    def _user_right_answer(self, uid, value):
        self._db.execute('answer_right', {
            'uid': uid,
            'platform': self._platform,
            'value': value,
        }, auto_commit=True)

    def _create_question_round(self):
        self._current_question = self._get_new_question()
        logging.info('New question id: %s', self._current_question['id'])
        self._db.execute(
            'create_question_round',
            (self._current_question['id'], int(time.time())),
            auto_commit=True
        )
        return self._current_question

    def _get_player_stats_timeframe(self, uid, start_time, end_time=None):
        rows = self._db.select_iter('get_timeframe_scores', {
            'uid': uid,
            'start_time': start_time,
            'end_time': end_time,
        }, as_map=True)

        for row in rows:
            yield row

    def _update_question_round_table(self, correct_uid = None):
        logging.info('Question winner player id: %s', correct_uid or 'none')
        params = {
            'player_id': None,
            'complete_time': int(time.time()),
        }
        if correct_uid is not None:
            player_id = self._db.select_one(
                'get_player_id',
                {'uid': correct_uid, 'platform': self._platform},
                as_map=True)['id']

            params['player_id'] = player_id

        self._db.execute(
            'update_question_round',
            params,
            auto_commit=True
        )

    def _command_exit(self, *_, message_payload, **kwargs):
        if kwargs.get('uid') == self._admin_uid:
            self._post_reply_handler('ok bye', message_payload=message_payload)
            self._do_exit()

    def _command_help(self, *_, message_payload, **__):
        template = '{}{:<20}{}'
        fmt = lambda x: template.format(self._command_prefix, x[0][0], x[1])
        c_list = [fmt(x) for x in self._commands() if x[1] is not None]
        commands = '\n'.join(c_list)
        formatted = self._pre_format_handler(commands)
        self._post_reply_handler(formatted, message_payload=message_payload)

    def _command_uptime(self, *_, message_payload, **__):
        uptime = int(time.time()) - int(self._starttime)
        uptime_str = str(datetime.timedelta(seconds=uptime))
        format_str = f'{uptime_str:0>8}'
        self._post_reply_handler(format_str, message_payload=message_payload)

    def _show_scores(self, days_ago, suppress_no_scores=False, message_payload=None):
        if days_ago == None:
            start = 0
            end = None
            title = 'Alltime Scores'
        else:
            start = self._timestamp_midnight(days_ago)
            end = self._timestamp_midnight(days_ago - 1)
            title = f'Scoreboard for {self._ftime(start)}'

        scores = list(self._get_player_stats_timeframe(None, start, end))

        if suppress_no_scores and len(scores) == 0:
            return

        for score in scores:
            # Get the current display name from slack, limit to 32 chars
            score['name'] = self._get_display_name_handler(score['uid'])[:32]

        title2 = '=' * len(title)
        scoreboard = self._format_scoreboard(scores)
        scoreboard_pre = f'{title}\n{title2}\n{scoreboard}'
        formatted = self._pre_format_handler(scoreboard_pre)
        if message_payload:
            self._post_reply_handler(formatted, message_payload)
        else:
            self._post_message_handler(formatted)

    @staticmethod
    def _do_exit():
        os.kill(os.getpid(), signal.SIGTERM)

    @staticmethod
    def _timestamp_midnight(days_ago=0):
        day = datetime.datetime.today() - datetime.timedelta(days=days_ago)
        return int(datetime.datetime.combine(day, datetime.time.min).timestamp())

    @staticmethod
    def _ftime(timestamp):
        return time.strftime('%A %B %d %Y',time.localtime(int(timestamp)))

    @staticmethod
    def _format_scoreboard(scores):
        cols = [
            ('rank', lambda x: x),
            ('name', lambda x: x),
            ('score', lambda x: f'{x:,}'),
            ('correct', lambda x: x),
        ]

        return tabulate([{col: fn(x[col]) for col, fn in cols} for x in scores], headers='keys')

    @staticmethod
    def _answer_variants(answer):
        answer_filters = [
            lambda x: [unidecode.unidecode(x)] if unidecode.unidecode(x) != x else [],
            lambda x: [re.sub(r'[0-9]+(?:[\.,][0-9]+)?', lambda y: num2words(y.group(0)), x)],
            lambda x: [x.replace(a, b) for a,b in [['&', 'and'],['%', 'percent']] if a in x],
            lambda x: [x[len(a):] for a in ['a ', 'an ', 'the '] if x.startswith(a)],
            lambda x: [''.join([a for a in x if a not in ' '])],
            lambda x: [''.join([a for a in x if a not in '\'().,"-'])],
        ]

        possible_answers = [answer.lower()]
        for answer_filter in answer_filters:
            for possible_answer in possible_answers:
                try:
                    possible_answers = list(set(
                        [*possible_answers, *answer_filter(possible_answer)]
                        ))
                except Exception as ex:
                    logging.exception(ex)

        return possible_answers

    @staticmethod
    def _do_check_answer(answer, correct_answer, match_character_count):
        correct_answer_variations = TriviaCore._answer_variants(correct_answer)
        given_answer_variations = TriviaCore._answer_variants(answer)

        for correct_answer_variation in correct_answer_variations:
            for given_answer_variation in given_answer_variations:
                min_match_len = min(match_character_count, len(correct_answer_variation))
                if (len(given_answer_variation.strip(' ')) >= min_match_len and
                    given_answer_variation.strip() in correct_answer_variation):
                    return True

        return False