from pathlib import Path
from urllib.parse import quote

import yaml


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "clash.yaml"
LINKEDIN_GROUP = "💼 LinkedIn"


def template():
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def test_linkedin_group_routes_full_service_family():
    doc = template()
    group = next(item for item in doc["proxy-groups"] if item["name"] == LINKEDIN_GROUP)
    assert group["proxies"] == ["✈️ Proxy", "DIRECT"]

    provider = doc["rule-providers"]["linkedin-services"]
    assert provider == {
        "type": "http", "behavior": "classical", "format": "yaml",
        "url": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Clash/LinkedIn/LinkedIn_No_Resolve.yaml",
        "path": "./ruleset/linkedin-services.yaml", "interval": 86400,
    }

    rules = doc["rules"]
    for rule in (
        "RULE-SET,linkedin-services,💼 LinkedIn",
        "GEOSITE,linkedin,💼 LinkedIn",
        "DOMAIN-SUFFIX,linkedin.com,💼 LinkedIn",
        "DOMAIN-SUFFIX,licdn.com,💼 LinkedIn",
        "DOMAIN-SUFFIX,lnkd.in,💼 LinkedIn",
        "DOMAIN-SUFFIX,linkedinlearning.com,💼 LinkedIn",
        "PROCESS-NAME,com.linkedin.android,💼 LinkedIn",
    ):
        assert rule in rules


def test_linkedin_domains_use_linkedin_policy_dns():
    policy = template()["dns"]["nameserver-policy"]
    target = "#" + quote(LINKEDIN_GROUP, safe="")
    for domain in (
        "+.linkedin.com", "+.linkedin.cn", "+.licdn.com", "+.licdn.cn",
        "+.lnkd.in", "+.linkedin-ei.com", "+.linkedinlearning.com",
    ):
        assert domain in policy
        assert all(target in resolver for resolver in policy[domain])
