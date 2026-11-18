"""Register all site adapters. Import this package for a populated ADAPTERS."""

from ucuzu.sites.amazon import AmazonAdapter
from ucuzu.sites.base import register
from ucuzu.sites.hepsiburada import HepsiburadaAdapter
from ucuzu.sites.n11 import N11Adapter
from ucuzu.sites.trendyol import TrendyolAdapter

register(HepsiburadaAdapter())
register(TrendyolAdapter())
register(N11Adapter())
register(AmazonAdapter())