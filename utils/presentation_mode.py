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
    # Every vector is returned twice -- once under "vectors" and once at
    # the top level -- because that is the shape run_full_osint_sweep()
    # produces (it ends with **results) and the shape the Master Dashboard
    # reads: the metric row reads findings["vectors"][...], while each
    # section render reads findings[...] directly. Returning only "vectors"
    # showed the headline counts but left every section reading
    # "Unavailable", which is exactly the wrong thing to happen on stage.
    vectors = {
            "footprint": {
                "status": "success",
                "module": "footprint",
                "count": 2,
                "records": [
                    {
                        "platform": "GitHub",
                        "category": "Development",
                        "confidence": "confirmed",
                        "profile_url": "https://github.com/janedoe_dev",
                        "reason": "Username matched exactly",
                    },
                    {
                        "platform": "Twitter",
                        "category": "Social Media",
                        "confidence": "possible",
                        "profile_url": "https://twitter.com/janedoe_dev",
                        "reason": "Username pattern match",
                    },
                ],
            },
            "email": {
                "status": "success",
                "module": "email",
                "count": 2,
                "records": [
                    {
                        "service": "Have I Been Pwned",
                        "vector": "Email in breach database",
                        "confidence": "confirmed",
                        "reason": "Found in Adobe 2013 breach (153M records)",
                    },
                    {
                        "service": "Gravatar",
                        "vector": "Email has public avatar",
                        "confidence": "confirmed",
                        "reason": "Gravatar profile located",
                    },
                ],
            },
            "github": {
                "status": "empty",
                "module": "github",
                "count": 0,
                "records": [],
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
                "count": 1,
                "cert_count": 1,
                "certificates": [
                    {
                        "domain": "example.com",
                        "issuer": "Let's Encrypt",
                        "valid_from": "2024-01-15",
                        "valid_until": "2025-01-15",
                    },
                ],
                "domain_info": {
                    "domain": "example.com",
                    "registrar": "GoDaddy",
                    "registered": "2012-03-20",
                },
            },
    }
    return {
        "summary": {
            "total_exposures": 4,
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
