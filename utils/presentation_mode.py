"""Presentation Mode: safe demo with mock data and instant results."""
import asyncio
from typing import Dict, Any

import profile_state

MOCK_PROFILE = {
    "name": "Jane Doe",
    "email": "jane.doe@example.com",
    "handle": "janedoe_dev",
    "domain": "example.com",
    "state": "NY",
}

async def get_mock_osint_findings() -> Dict[str, Any]:
    """Return impressive mock OSINT findings for presentation."""
    await asyncio.sleep(1.5)  # Dramatic effect
    email_records = [
        {
            "platform": "Have I Been Pwned (Adobe)",
            "service": "Have I Been Pwned",
            "vector": "Email in breach database",
            "confidence": "confirmed",
            "reason": "Found in Adobe 2013 breach (153M accounts exposed: email, password hint)",
            "breach": "Adobe 2013",
            "url": "https://haveibeenpwned.com",
        },
        {
            "platform": "Have I Been Pwned (Canva)",
            "service": "Have I Been Pwned",
            "vector": "Email in breach database",
            "confidence": "confirmed",
            "reason": "Found in Canva 2019 breach (137M accounts exposed: email, name, city)",
            "breach": "Canva 2019",
            "url": "https://haveibeenpwned.com",
        },
        {
            "platform": "Gravatar",
            "service": "Gravatar",
            "vector": "Email has public avatar & profile",
            "confidence": "confirmed",
            "reason": "Public Gravatar profile located (@janedoe_dev)",
            "profile_url": "https://gravatar.com/janedoe_dev",
            "avatar_url": "https://www.gravatar.com/avatar/d41d8cd98f00b204e9800998ecf8427e?s=128&d=404",
        },
        {
            "platform": "Libravatar",
            "service": "Libravatar",
            "vector": "Federated avatar presence",
            "confidence": "confirmed",
            "reason": "Libravatar profile and image hash discovered",
            "avatar_url": "https://seccdn.libravatar.org/avatar/d41d8cd98f00b204e9800998ecf8427e?s=128&d=404",
        },
        {
            "platform": "Spotify",
            "service": "Spotify",
            "vector": "Media streaming account",
            "confidence": "confirmed",
            "reason": "Account registered with public playlist activity",
            "profile_url": "https://open.spotify.com",
        },
        {
            "platform": "Adobe ID",
            "service": "Adobe",
            "vector": "Creative Cloud registered user",
            "confidence": "confirmed",
            "reason": "Adobe ID registered for this email address",
            "profile_url": "https://account.adobe.com",
        },
        {
            "platform": "GitHub",
            "service": "GitHub",
            "vector": "Developer commit email association",
            "confidence": "confirmed",
            "reason": "Public commits authored by jane.doe@example.com linked to @janedoe_dev",
            "profile_url": "https://github.com/janedoe_dev",
        },
        {
            "platform": "OnlyFans",
            "service": "OnlyFans",
            "vector": "Subscription platform account",
            "confidence": "confirmed",
            "reason": "Account registered on OnlyFans",
            "profile_url": "https://onlyfans.com",
        },
        {
            "platform": "Pornhub",
            "service": "Pornhub",
            "vector": "Adult entertainment account",
            "confidence": "confirmed",
            "reason": "Account registered on Pornhub",
            "profile_url": "https://www.pornhub.com",
        },
        {
            "platform": "XVideos",
            "service": "XVideos",
            "vector": "Adult entertainment account",
            "confidence": "confirmed",
            "reason": "Account registered on XVideos",
            "profile_url": "https://www.xvideos.com",
        },
        {
            "platform": "Stripchat",
            "service": "Stripchat",
            "vector": "Live streaming account",
            "confidence": "confirmed",
            "reason": "Account registered on Stripchat",
            "profile_url": "https://stripchat.com",
        },
        {
            "platform": "Pinterest",
            "service": "Pinterest",
            "vector": "Social curation account",
            "confidence": "confirmed",
            "reason": "Pinterest account registered",
            "profile_url": "https://www.pinterest.com",
        },
        {
            "platform": "Duolingo",
            "service": "Duolingo",
            "vector": "Education / language learning",
            "confidence": "confirmed",
            "reason": "Duolingo profile (@janedoe_dev) linked to email",
            "profile_url": "https://www.duolingo.com/profile/janedoe_dev",
        },
        {
            "platform": "Chess.com",
            "service": "Chess.com",
            "vector": "Gaming platform account",
            "confidence": "confirmed",
            "reason": "Chess.com user account active",
            "profile_url": "https://www.chess.com",
        },
        {
            "platform": "Substack",
            "service": "Substack",
            "vector": "Publishing / newsletter author",
            "confidence": "confirmed",
            "reason": "Substack profile active",
            "profile_url": "https://substack.com/@janedoe_dev",
        },
        {
            "platform": "PGP Keys",
            "service": "PGP Keys",
            "vector": "Published public cryptographic key",
            "confidence": "confirmed",
            "reason": "PGP public encryption key published on OpenPGP keyserver",
            "profile_url": "https://keys.openpgp.org",
        },
        {
            "platform": "Imgur",
            "service": "Imgur",
            "vector": "Media hosting account",
            "confidence": "confirmed",
            "reason": "Imgur account registered",
            "profile_url": "https://imgur.com",
        },
        {
            "platform": "X (Twitter)",
            "service": "X (Twitter)",
            "vector": "Social network account",
            "confidence": "confirmed",
            "reason": "Account registered on X (Twitter)",
            "profile_url": "https://x.com",
        },
        {
            "platform": "eBay",
            "service": "eBay",
            "vector": "Ecommerce account",
            "confidence": "confirmed",
            "reason": "Account registered on eBay",
            "profile_url": "https://www.ebay.com",
        },
    ]

    footprint_records = [
        {
            "platform": "GitHub (User)",
            "category": "Development",
            "confidence": "confirmed",
            "profile_url": "https://github.com/janedoe_dev",
            "reason": "Username matched exactly",
        },
        {
            "platform": "X / Twitter",
            "category": "Social Media",
            "confidence": "confirmed",
            "profile_url": "https://twitter.com/janedoe_dev",
            "reason": "Username matched exactly",
        },
        {
            "platform": "Reddit",
            "category": "Social",
            "confidence": "confirmed",
            "profile_url": "https://reddit.com/user/janedoe_dev",
            "reason": "User profile active",
        },
        {
            "platform": "Instagram",
            "category": "Social Media",
            "confidence": "confirmed",
            "profile_url": "https://instagram.com/janedoe_dev",
            "reason": "Public profile active",
        },
        {
            "platform": "TikTok",
            "category": "Media",
            "confidence": "confirmed",
            "profile_url": "https://tiktok.com/@janedoe_dev",
            "reason": "Public creator profile active",
        },
        {
            "platform": "Twitch",
            "category": "Streaming",
            "confidence": "confirmed",
            "profile_url": "https://twitch.tv/janedoe_dev",
            "reason": "Streaming channel active",
        },
        {
            "platform": "Steam",
            "category": "Gaming",
            "confidence": "confirmed",
            "profile_url": "https://steamcommunity.com/id/janedoe_dev",
            "reason": "Steam community profile active",
        },
        {
            "platform": "Medium",
            "category": "Publishing",
            "confidence": "confirmed",
            "profile_url": "https://medium.com/@janedoe_dev",
            "reason": "Author blog active",
        },
        {
            "platform": "OnlyFans",
            "category": "Adult Content",
            "confidence": "confirmed",
            "profile_url": "https://onlyfans.com/janedoe_dev",
            "reason": "Creator account active",
        },
        {
            "platform": "Pornhub",
            "category": "Adult Content",
            "confidence": "confirmed",
            "profile_url": "https://pornhub.com/users/janedoe_dev",
            "reason": "User profile active",
        },
    ]

    github_records = [
        {
            "repository": "janedoe_dev/dotfiles",
            "platform": "GitHub",
            "category": "Public Code",
            "confidence": "confirmed",
            "reason": "Exposed personal email in commit logs",
            "profile_url": "https://github.com/janedoe_dev/dotfiles",
        },
        {
            "repository": "janedoe_dev/config-vault",
            "platform": "GitHub",
            "category": "Public Code",
            "confidence": "confirmed",
            "reason": "Public repository with personal name in README",
            "profile_url": "https://github.com/janedoe_dev/config-vault",
        },
    ]

    infra_records = [
        {
            "subdomain": "www.example.com",
            "domain": "example.com",
            "issuer": "Let's Encrypt Authority X3",
            "valid_from": "2024-01-15",
            "valid_until": "2025-01-15",
            "confidence": "confirmed",
            "reason": "Active SSL certificate transparency log",
        },
    ]

    domain_info = {
        "domain": "example.com",
        "registrar": "GoDaddy.com, LLC",
        "registered": "2012-03-20",
        "confidence": "confirmed",
        "reason": "Public WHOIS registration",
    }

    vectors = {
        "footprint": {
            "status": "success",
            "module": "footprint",
            "count": len(footprint_records),
            "records": footprint_records,
        },
        "email": {
            "status": "success",
            "module": "email",
            "count": len(email_records),
            "records": email_records,
        },
        "github": {
            "status": "success",
            "module": "github",
            "count": len(github_records),
            "records": github_records,
        },
        "sec": {
            "status": "empty",
            "module": "sec",
            "count": 0,
            "records": [],
        },
        "fec": {
            "status": "empty",
            "module": "fec",
            "count": 0,
            "records": [],
        },
        "courtlistener": {
            "status": "empty",
            "module": "courtlistener",
            "count": 0,
            "records": [],
        },
        "infrastructure": {
            "status": "success",
            "module": "infrastructure",
            "count": len(infra_records),
            "cert_count": len(infra_records),
            "certificates": infra_records,
            "domain_info": domain_info,
        },
    }

    total_exposures = (
        len(footprint_records) +
        len(email_records) +
        len(github_records) +
        len(infra_records)
    )

    return {
        "summary": {
            "total_exposures": total_exposures,
        },
        "vectors": vectors,
        **vectors,
    }


def populate_demo_profile(state):
    """Populate session state with realistic mock profile data.

    Writes the pf_* form fields *and* the canonical profile. Filling only
    the form fields left the dossier reading an empty profile until the
    presenter walked over to the Profile tab and pressed Save -- so
    enabling presentation mode and going straight to the dossier showed
    "Missing required fields", which is the one thing it exists to avoid.

    Safe to call from the sidebar because the sidebar renders before the
    page body: assigning to a pf_* key after its widget has been created
    in the same run is what Streamlit rejects.
    """
    state["pf_first_name"] = "Jane"
    state["pf_last_name"] = "Doe"
    state["pf_middle_name"] = ""
    state["pf_email"] = "jane.doe@example.com"
    state["pf_phone"] = "(555) 123-4567"
    state["pf_city"] = "New York"
    state["pf_state"] = "NY"
    state["pf_zip"] = "10001"
    state["pf_handle"] = "janedoe_dev"
    state["pf_domain"] = "example.com"
    state["pf_birth_year"] = 1990
    state["pf_historical_zips"] = "94105, 60601"
    state["pf_associated_name"] = ""
    state["pf_shared_addresses"] = ""
    state["pf_shared_phones"] = ""
    state["pf_shared_loyalty"] = ""

    profile_state.sync_profile(state, {
        "full_name": f"{MOCK_PROFILE['name']}",
        "email": MOCK_PROFILE["email"],
        "handle": MOCK_PROFILE["handle"],
        "domain": MOCK_PROFILE["domain"],
        "city": "New York",
        "state": MOCK_PROFILE["state"],
        "zip_code": "10001",
        "phone": "(555) 123-4567",
    })
