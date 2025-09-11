# -*- coding: utf-8 -*-
# @Author  : 
# @Time    : 

import hashlib
import re
import sys
import time
import requests
sys.path.append('..')
from base.spider import Spider


class Spider(Spider):
    def getName(self):
        return "JieYingShi"

    def init(self, extend):
        self.home_url = 'https://jp.spankbang.com/'
        self.error_url = ''
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
