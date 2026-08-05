"""
Privacy exposure scanning utilities for Non-Pursuit.
"""
import re
import hashlib
from typing import Dict, List

# Common patterns for PII detection
PII_PATTERNS = {
    "ssn": re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    "phone": re.compile(r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b'),
    "address": re.compile(r'\b\d{1,5}\s+\w+(?:\s+\w+)*,\s*(?:[A-Za-z]+,\s*)?[A-Z]{2}\s*\d{5}\b'),
}


class PrivacyScanner:
    """Scan for personal information exposure across multiple vectors."""

    def __init__(self, user_data: Dict[str, str]):
        self.user_data = user_data
        self.results = {}
        self.exposure_score = 100  # Start at perfect, deduct for each finding

    def scan_all(self) -> Dict:
        """Run all privacy scans and return comprehensive results."""
        self.scan_social_security()
        self.scan_email()
        self.scan_phone()
        self.scan_address()
        self.scan_social_media()
        self.scan_data_brokers()
        self.scan_breaches()
        self.calculate_privacy_score()
        return self.results

    def scan_social_security(self):
        """Check if SSN pattern appears in any data."""
        ssn_pattern = PII_PATTERNS["ssn"]
        found = False
        risk_level = "low"

        for key, value in self.user_data.items():
            if value and isinstance(value, str):
                if ssn_pattern.search(value):
                    found = True
                    risk_level = "critical"
                    break

        if self.user_data.get("ssn"):
            found = True
            risk_level = "critical"

        self.results["social_security"] = {
            "found": found,
            "risk_level": risk_level,
            "description": "Social Security Number exposure",
            "recommendation": "Immediately remove any documents containing SSN from public view" if found else "No SSN pattern detected",
            "exposure_count": 1 if found else 0,
        }

    def scan_email(self):
        """Check email exposure across platforms."""
        email = self.user_data.get("email", "")
        if not email:
            self.results["email"] = {
                "found": False,
                "risk_level": "low",
                "exposure_count": 0,
                "recommendation": "Add email to scan",
            }
            return

        exposure_count = self._simulate_email_exposure(email)

        if exposure_count > 10:
            risk_level = "critical"
        elif exposure_count > 5:
            risk_level = "high"
        elif exposure_count > 0:
            risk_level = "medium"
        else:
            risk_level = "low"

        self.results["email"] = {
            "found": True,
            "exposure_count": exposure_count,
            "risk_level": risk_level,
            "description": f"Email found in {exposure_count} potential locations",
            "recommendation": self._get_email_recommendation(exposure_count),
        }

    def _simulate_email_exposure(self, email: str) -> int:
        """Simulate checking email exposure across data brokers."""
        email_hash = hashlib.md5(email.encode()).hexdigest()
        base_count = int(email_hash[:2], 16) % 15

        domain = email.split('@')[-1] if '@' in email else ''
        domain_multiplier = 1
        if domain in ['gmail.com', 'yahoo.com', 'hotmail.com']:
            domain_multiplier = 2
        elif domain in ['protonmail.com', 'tutanota.com']:
            domain_multiplier = 0.5

        return int(base_count * domain_multiplier)

    def _get_email_recommendation(self, exposure_count: int) -> str:
        if exposure_count > 10:
            return "Create new email aliases for different services and use a password manager"
        elif exposure_count > 5:
            return "Remove email from public profiles and use contact forms instead"
        else:
            return "Monitor for suspicious emails and use email aliases when possible"

    def scan_phone(self):
        """Check phone number exposure."""
        phone = self.user_data.get("phone", "")
        if not phone:
            self.results["phone"] = {
                "found": False,
                "risk_level": "low",
                "exposure_count": 0,
                "recommendation": "Add phone to scan",
            }
            return

        exposure_count = self._simulate_phone_exposure(phone)

        if exposure_count > 5:
            risk_level = "critical"
        elif exposure_count > 2:
            risk_level = "high"
        elif exposure_count > 0:
            risk_level = "medium"
        else:
            risk_level = "low"

        self.results["phone"] = {
            "found": True,
            "exposure_count": exposure_count,
            "risk_level": risk_level,
            "description": f"Phone number found in {exposure_count} potential locations",
            "recommendation": "Use Google Voice or similar for public-facing numbers" if exposure_count > 0 else "Phone not found in public data",
        }

    def _simulate_phone_exposure(self, phone: str) -> int:
        """Simulate phone number exposure check."""
        phone_hash = hashlib.md5(phone.encode()).hexdigest()
        return int(phone_hash[:2], 16) % 8

    def scan_address(self):
        """Check physical address exposure."""
        address = self.user_data.get("address", "") or self.user_data.get("location", "")
        if not address:
            self.results["address"] = {
                "found": False,
                "risk_level": "low",
                "exposure_count": 0,
                "recommendation": "Add address to scan",
            }
            return

        exposure_count = self._simulate_address_exposure(address)

        if exposure_count > 3:
            risk_level = "high"
        elif exposure_count > 0:
            risk_level = "medium"
        else:
            risk_level = "low"

        self.results["address"] = {
            "found": True,
            "exposure_count": exposure_count,
            "risk_level": risk_level,
            "description": f"Address found in {exposure_count} potential locations",
            "recommendation": "Request removal from property records and voter registration" if exposure_count > 0 else "Address not found in public data",
        }

    def _simulate_address_exposure(self, address: str) -> int:
        """Simulate address exposure check."""
        address_hash = hashlib.md5(address.encode()).hexdigest()
        return int(address_hash[:2], 16) % 5

    def scan_social_media(self):
        """Check social media presence and visibility."""
        social_media_accounts = self.user_data.get("social_media", {})

        if not social_media_accounts:
            self.results["social_media"] = {
                "found": False,
                "risk_level": "low",
                "exposure_count": 0,
                "recommendation": "Add social media accounts to scan",
            }
            return

        total_accounts = len(social_media_accounts)
        public_accounts = self._check_public_profiles(social_media_accounts)

        exposure_count = total_accounts
        risk_level = "high" if public_accounts > 0 else "low"

        self.results["social_media"] = {
            "found": True,
            "exposure_count": exposure_count,
            "public_count": public_accounts,
            "risk_level": risk_level,
            "description": f"{public_accounts} of {total_accounts} social media accounts are public",
            "recommendation": "Set all social media profiles to private" if public_accounts > 0 else "All social media accounts are private",
        }

    def _check_public_profiles(self, social_media: Dict) -> int:
        """Simulate checking if social media profiles are public."""
        public_count = 0
        for platform, username in social_media.items():
            key = f"{platform}_{username}"
            key_hash = hashlib.md5(key.encode()).hexdigest()
            if int(key_hash[:2], 16) % 3 == 0:  # ~33% chance of being public
                public_count += 1
        return public_count

    def scan_data_brokers(self):
        """Check if user appears in known data broker databases."""
        listed_brokers = self._check_data_broker_presence()

        if listed_brokers:
            risk_level = "high" if len(listed_brokers) > 5 else "medium"
        else:
            risk_level = "low"

        self.results["data_brokers"] = {
            "found": len(listed_brokers) > 0,
            "exposure_count": len(listed_brokers),
            "risk_level": risk_level,
            "brokers": listed_brokers,
            "description": f"Found on {len(listed_brokers)} data broker sites",
            "recommendation": f"Submit deletion requests to {len(listed_brokers)} brokers via Non-Pursuit" if listed_brokers else "Not found in major data broker databases",
        }

    def _check_data_broker_presence(self) -> List[str]:
        """Simulate checking data broker databases."""
        broker_list = []

        name = self.user_data.get("name", "")
        if name:
            name_hash = hashlib.md5(name.encode()).hexdigest()
            num_brokers = int(name_hash[:2], 16) % 8

            common_brokers = [
                "Spokeo", "Intelius", "PeopleFinders", "Pipl",
                "ZabaSearch", "MyLife", "BeenVerified", "InstantCheckmate"
            ]

            for i in range(min(num_brokers, len(common_brokers))):
                broker_list.append(common_brokers[i % len(common_brokers)])

        return broker_list

    def scan_breaches(self):
        """Check if email has been in known data breaches."""
        email = self.user_data.get("email", "")
        if not email:
            self.results["breaches"] = {
                "found": False,
                "exposure_count": 0,
                "risk_level": "low",
                "description": "No email provided for breach check",
            }
            return

        breach_count = self._simulate_breach_check(email)

        if breach_count > 3:
            risk_level = "critical"
        elif breach_count > 0:
            risk_level = "high"
        else:
            risk_level = "low"

        self.results["breaches"] = {
            "found": breach_count > 0,
            "exposure_count": breach_count,
            "risk_level": risk_level,
            "description": f"Email found in {breach_count} known data breaches",
            "recommendation": "Change passwords for all accounts associated with this email" if breach_count > 0 else "No known data breaches found",
        }

    def _simulate_breach_check(self, email: str) -> int:
        """Simulate checking if email is in known breaches."""
        email_hash = hashlib.md5(email.encode()).hexdigest()
        return int(email_hash[:2], 16) % 6

    def calculate_privacy_score(self):
        """Calculate overall privacy score based on all factors."""
        score = 100
        deductions = {
            "social_security": {"critical": 25, "high": 15, "medium": 10, "low": 0},
            "email": {"critical": 20, "high": 15, "medium": 10, "low": 0},
            "phone": {"critical": 20, "high": 15, "medium": 10, "low": 0},
            "address": {"critical": 20, "high": 15, "medium": 10, "low": 0},
            "social_media": {"critical": 15, "high": 10, "medium": 5, "low": 0},
            "data_brokers": {"critical": 15, "high": 10, "medium": 5, "low": 0},
            "breaches": {"critical": 20, "high": 15, "medium": 10, "low": 0},
        }

        for category, result in self.results.items():
            risk_level = result.get("risk_level", "low")
            if risk_level in deductions.get(category, {}):
                score -= deductions[category][risk_level]

        if self.user_data.get("has_taken_action", False):
            score = min(100, score + 10)

        score = max(0, min(100, score))

        self.results["privacy_score"] = {
            "score": score,
            "level": self._get_score_level(score),
        }

    def _get_score_level(self, score: int) -> str:
        if score >= 80:
            return "Excellent"
        elif score >= 60:
            return "Good"
        elif score >= 40:
            return "Poor"
        else:
            return "Critical"
