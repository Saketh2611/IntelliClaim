class ClaimError(Exception):
    """Base class for all claim errors"""
    pass

class DocumentValidationError(ClaimError):
    """Wrong or missing document types"""
    def __init__(self, message: str, details: dict):
        self.message = message
        self.details = details
        super().__init__(message)

class UnreadableDocumentError(ClaimError):
    """Document too blurry or corrupt to extract"""
    def __init__(self, document_id: str, file_name: str):
        self.document_id = document_id
        self.file_name   = file_name
        super().__init__(f"Document {file_name} is unreadable")

class PatientMismatchError(ClaimError):
    """Documents belong to different patients"""
    def __init__(self, mismatches: list[dict]):
        self.mismatches = mismatches
        super().__init__("Documents belong to different patients")

class MemberNotFoundError(ClaimError):
    """member_id not found in DB"""
    pass

class PolicyLoadError(ClaimError):
    """Could not load policy_terms.json"""
    pass

class ComponentFailureError(ClaimError):
    """An agent failed mid-pipeline — used for graceful degradation"""
    def __init__(self, component: str, reason: str):
        self.component = component
        self.reason    = reason
        super().__init__(f"{component} failed: {reason}")