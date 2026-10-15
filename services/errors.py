class LikedTrackReplicaPendingError(RuntimeError):
    """Report that Spotify changed a favorite but its local database replica failed."""

    def __init__(self, action: str):
        self.action = action
        super().__init__(
            f"Spotify completed the {action} action, but the local favorite could not be saved."
        )
