# -*- coding: utf-8 -*-
# add by xieke 2018.6 for sending vm_data to zabbix
# 主要实现方法是通过ceilometer暴露的restful接口

import datetime
import requests
import json
from collections import defaultdict
from concurrent import futures

from futurist import periodics
import cotyledon
from oslo_utils import timeutils
from oslo_config import cfg
from oslo_log import log
from ceilometer import utils
from pyzabbix import ZabbixMetric, ZabbixSender

#cfg.CONF(default_config_files=['/etc/ceilometer/ceilometer.conf'])
zabbix_dispatcher_opts = [
    cfg.StrOpt('agent_server',
               default = '10.100.10.20',
               help = 'fu_jian_yun, use proxy to receive data'
               ),
    cfg.IntOpt('interval',
               default = 86400,  # unit:S
               help = 'time interval of process run'
               ),
    cfg.StrOpt('username',
               default = 'admin',
               help = ''),
    cfg.StrOpt('keystone_pwd',
               default = 'd48f9d20cf524af862cd',
               help = ''),
    cfg.StrOpt('host_name',
               default = 'fujian_statistics',
               help = 'host_name in zabbix server used to collect data')
]
cfg.CONF.register_opts(zabbix_dispatcher_opts, group="zabbix")

LOG = log.getLogger(__name__)

class ZabbixSenderService(cotyledon.Service):
    '''listen for the Zabbix_Sender Service'''
    def __init__(self,worker_id):
        self.time_end = timeutils.isotime(datetime.datetime.utcnow())
        self.time_start = timeutils.isotime(datetime.datetime.utcnow() - datetime.timedelta(days=1))
        self.user = cfg.CONF.zabbix.username
        self.password = cfg.CONF.zabbix.keystone_pwd
        self.sender = ZabbixSender(cfg.CONF.zabbix.agent_server)
        super(ZabbixSenderService, self).__init__(worker_id)

    def get_token(self):
        auth_url = 'http://controller:35357/v3/auth/tokens/'
        body = {
            "auth": {
                "identity": {
                    "methods": [
                        "password"
                    ],
                    "password": {
                        "user": {
                            "domain": {
                                "name": "Default"
                            },
                            "name": self.user,
                            "password": self.password,
                        }
                    }
                },
                "scope": {
                    "project": {
                        "domain": {
                            "name": "Default"
                        },
                        "name": "admin"
                    }
                }
            }
        }
        result = requests.post(url=auth_url, data=json.dumps(body))
        if result.status_code != 201:
            LOG.warning('response code is %s' % result.status_code)
            raise Exception('request failed in getting token.')
        else:
            return result.headers['X-Subject-Token']

    def get_sum_memory_and_disk(self):
        token = self.get_token()
        url = ('http://controller:8777/'
                'v2/instancestates/cluster_usage?&timeStart=%s&timeEnd=%s&detail=true'
                % (self.time_start, self.time_end))
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/67.0.3396.79 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'X-Auth-Token': token,
            }
        res = requests.get(url=url, headers=headers)
        data_dic = defaultdict(lambda: 0)
        for item in res.json():
            data_dic['memory_sum'] += item['memory']['sum']
            data_dic['memory_usage_sum'] += item['memory']['usage']
            data_dic['disk_sum'] += item['disk']['sum']
            data_dic['disk_usage_sum'] += item['disk']['usage']
            data_dic['vm_num_sum'] += item['vm_num']
            data_dic['cpu_sum'] += item['cpu']['sum']
            data_dic['cpu_usage_sum'] += item['cpu']['usage']
        return data_dic

    def send_data_to_zabbix(self):
        data_sum = self.get_sum_memory_and_disk()
        packet = []
        for name, value in data_sum.items():
            packet.append(ZabbixMetric(cfg.CONF.zabbix.host_name,
                                       name,
                                       value))
        response_info = self.sender.send(packet)
        LOG.info('Response from zabbix_server:%s' % response_info)

    def start_task(self):
        LOG.info('Start the polling task of zabbix_sender, interval is %ss' % cfg.CONF.zabbix.interval)
        polling_periodics = periodics.PeriodicWorker.create(
           [], executor_factory=lambda:
           futures.ThreadPoolExecutor(max_workers=1))

        @periodics.periodic(spacing=cfg.CONF.zabbix.interval, run_immediately=True)
        def task(to_run_task):
            to_run_task()
        polling_periodics.add(task, self.send_data_to_zabbix)
        utils.spawn_thread(polling_periodics.start, allow_empty=True)

    def run(self):
        self.start_task()

    def terminate(self):
        super(ZabbixSenderService,self).terminate()

