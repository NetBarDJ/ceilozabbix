# -*- encoding: utf-8 -*-
# 定制数据发送到zabbix
import cotyledon
from oslo_config import cfg

from ceilometer import zabbix_sender
from ceilometer import service

CONF = cfg.CONF


def main():
    service.prepare_service()
    sm = cotyledon.ServiceManager()
    sm.add(zabbix_sender.ZabbixSenderService, workers=CONF.ceilozabbix.workers)
    sm.run()
