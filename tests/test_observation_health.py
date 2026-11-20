"""No-post days may publish; failed or unverified discovery may not."""
import copy
from datetime import datetime
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from monitor import observation_health_errors


class ObservationHealthTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.fromisoformat('2026-11-21T06:30:00+09:00')
        self.health={'schema_version':1,'sources':{shop:{
            'checked_at':'2026-11-21T06:00:00+09:00','listing_status':'ok',
            'status':'ok','discovered_count':20,'new_url_count':0,'latest_article_date':'2026-11-16',
            'article_attempted':0,'article_succeeded':0,'article_failed':0,
            'unresolved_url_count':0,'listing_regressed':False
        } for shop in ('hammers','ms','290')}}

    def test_old_report_with_verified_poll_is_not_scraper_failure(self):
        self.assertEqual(observation_health_errors(self.health,self.now),[])

    def test_empty_missing_and_failed_sources_are_rejected(self):
        for change in ({'status':'partial'},{'listing_status':'error'},{'discovered_count':0},
                       {'discovered_count':True},{'article_failed':1},{'unresolved_url_count':1},
                       {'listing_regressed':True},{'article_attempted':2},{'new_url_count':1}):
            with self.subTest(change=change):
                health=copy.deepcopy(self.health)
                health['sources']['hammers'].update(change)
                self.assertTrue(observation_health_errors(health,self.now))
        self.assertTrue(observation_health_errors({'schema_version':1,'sources':{}},self.now))

    def test_stale_future_naive_checks_do_not_certify_latest_sources(self):
        for stamp in ('2026-11-19T06:00:00+09:00','2026-11-22T06:00:00+09:00','2026-11-21T06:00:00',None):
            with self.subTest(stamp=stamp):
                health=copy.deepcopy(self.health)
                health['sources']['ms']['checked_at']=stamp
                self.assertTrue(observation_health_errors(health,self.now))
