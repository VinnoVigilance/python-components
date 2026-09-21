import os
import re
import subprocess
from pathlib import Path


_FULL_GIT_SHA_PATTERN = re.compile(
    r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$"
)


class PipelineVersionError(RuntimeError):
    """
    Raised when pipeline_version cannot be
    resolved or validated.
    """


class PipelineVersionService:
    """
    Resolves the Git commit hash used to execute
    the pipeline.

    The resolved value will later be stored in:

        core.media_record.pipeline_version
    """

    ENVIRONMENT_VARIABLES = (
        "PIPELINE_VERSION",
        "GIT_COMMIT_SHA",
        "CI_COMMIT_SHA",
        "GITHUB_SHA",
    )

    @classmethod
    def resolve(
        cls,
        repository_root: str | Path,
    ) -> str:
        """
        Resolve pipeline_version.

        Resolution order:

        1. Environment variable provided by deployment/CI
        2. Current local Git commit

        Returns:
            Full lowercase Git commit hash.
        """

        environment_version = (
            cls._read_from_environment()
        )

        if environment_version is not None:
            return environment_version

        repository_path = Path(
            repository_root
        ).resolve()

        return cls._read_from_git(
            repository_root=repository_path
        )

    @classmethod
    def validate(
        cls,
        pipeline_version: str,
    ) -> str:
        """
        Validate an explicitly supplied pipeline version.

        Only a full Git SHA is accepted:
        - 40 characters for SHA-1 repositories
        - 64 characters for SHA-256 repositories
        """

        normalized_version = str(
            pipeline_version
        ).strip().lower()

        if not _FULL_GIT_SHA_PATTERN.fullmatch(
            normalized_version
        ):
            raise PipelineVersionError(
                "pipeline_version must be a full "
                "40-character or 64-character "
                "Git commit hash."
            )

        return normalized_version

    @classmethod
    def _read_from_environment(
        cls,
    ) -> str | None:
        """
        Read a Git commit hash supplied by CI/CD.

        This is useful when the application is deployed
        without the .git directory, such as a Docker image.
        """

        for variable_name in (
            cls.ENVIRONMENT_VARIABLES
        ):

            value = os.getenv(
                variable_name
            )

            if value is None:
                continue

            cleaned_value = value.strip()

            if not cleaned_value:
                continue

            return cls.validate(
                cleaned_value
            )

        return None

    @classmethod
    def _read_from_git(
        cls,
        repository_root: Path,
    ) -> str:
        """
        Read the current full commit hash from Git.
        """

        if not repository_root.exists():
            raise PipelineVersionError(
                "Repository root does not exist: "
                f"{repository_root}"
            )

        try:
            completed_process = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--verify",
                    "HEAD",
                ],
                cwd=repository_root,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )

        except FileNotFoundError as error:
            raise PipelineVersionError(
                "Git executable was not found. "
                "Install Git or set the "
                "PIPELINE_VERSION environment variable."
            ) from error

        except subprocess.TimeoutExpired as error:
            raise PipelineVersionError(
                "Git command timed out while resolving "
                "pipeline_version."
            ) from error

        except subprocess.CalledProcessError as error:
            error_message = (
                error.stderr
                or error.stdout
                or "Unknown Git error."
            ).strip()

            raise PipelineVersionError(
                "Could not resolve pipeline_version. "
                f"Git error: {error_message}"
            ) from error

        commit_hash = (
            completed_process.stdout
            .strip()
        )

        if not commit_hash:
            raise PipelineVersionError(
                "Git returned an empty commit hash."
            )

        return cls.validate(
            commit_hash
        )