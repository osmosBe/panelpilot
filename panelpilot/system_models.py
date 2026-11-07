"""Small, validated surface shared by the web app and privileged local helper."""
import re
from pathlib import PurePosixPath
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, field_validator


class Maintenance(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = True
    update_time: str = '03:00'
    only_ac: bool = True
    reboot_enabled: bool = True
    reboot_day: Literal['Mon','Tue','Wed','Thu','Fri','Sat','Sun'] = 'Sun'
    reboot_time: str = '05:00'
    timezone: str = 'Europe/Vienna'

    @field_validator('update_time','reboot_time')
    @classmethod
    def valid_time(cls, value):
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',value):
            raise ValueError('Uhrzeit muss HH:MM sein.')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls,value):
        if not re.fullmatch(r'[A-Za-z_]+(?:/[A-Za-z0-9_+.-]+)*',value) or '..' in value:
            raise ValueError('Ungültige Zeitzone.')
        try: ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError): raise ValueError('Unbekannte Zeitzone.') from None
        return value


class NFS(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = False
    host: str = ''
    export: str = ''
    version: Literal['3','4.1','4.2'] = '3'

    @field_validator('host')
    @classmethod
    def valid_host(cls,value):
        if value and (len(value)>253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in value.split('.'))):
            raise ValueError('Bitte einen DNS-Namen oder eine IPv4-Adresse eingeben.')
        return value

    @field_validator('export')
    @classmethod
    def valid_export(cls,value):
        if value and (len(value)>1024 or not value.startswith('/') or '..' in PurePosixPath(value).parts or re.search(r'[\s\x00-\x1f\x7f\\:%]',value)):
            raise ValueError('Bitte einen absoluten NFS-Exportpfad ohne Leerzeichen eingeben.')
        return value

    def require_source(self):
        if not self.host or not self.export:
            raise ValueError('Server und Exportpfad fehlen.')

