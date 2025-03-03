import logging
import os
import subprocess
import sys
import time
import webbrowser

import boto3
import pyperclip
from botocore.exceptions import ClientError


# Logging configuration
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(__name__)


# Boto3 session and client setup
def setup_boto3_session(profile_name="devsecops"):
    session = boto3.Session(profile_name=profile_name)
    return session.client("codecommit"), session


# Get current branch name
def get_current_branch():
    return (
        subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        .strip()
        .decode("utf-8")
    )


# Check for existing PRs
def check_existing_pr(repository, source_ref, destination_ref, codecommit):
    response = codecommit.list_pull_requests(
        repositoryName=repository, pullRequestStatus="OPEN"
    )
    for pr_id in response["pullRequestIds"]:
        pr_details = codecommit.get_pull_request(pullRequestId=pr_id)
        pr = pr_details["pullRequest"]
        if (
            pr["pullRequestTargets"][0]["sourceReference"].split("/")[-1] == source_ref
            and pr["pullRequestTargets"][0]["destinationReference"].split("/")[-1]
            == destination_ref
        ):
            return pr_id
    return None


# Open PR in Chrome browser
def open_pr_in_chrome(repository, pr_id, devsecops_session):
    region = devsecops_session.region_name
    pr_url = f"https://{region}.console.aws.amazon.com/codesuite/codecommit/repositories/{repository}/pull-requests/{pr_id}/details"
    chrome_path = "C:/Program Files/Google/Chrome/Application/chrome.exe %s"
    try:
        webbrowser.get(chrome_path).open(pr_url)
        logger.info(f"Opened PR in Chrome: {pr_url}")
        return pr_url
    except Exception as e:
        logger.error(f"Failed to open Chrome: {e}")
        logger.info(f"PR URL: {pr_url}")


# Get PR description
def get_pr_description(pr_id, repository, codecommit):
    pr_details = codecommit.get_pull_request(pullRequestId=pr_id)
    pr = pr_details["pullRequest"]
    source_branch = pr["pullRequestTargets"][0]["sourceReference"].split("/")[-1]
    target_branch = pr["pullRequestTargets"][0]["destinationReference"].split("/")[-1]
    merge_base_commit = pr["pullRequestTargets"][0]["mergeBase"]

    description = f"""
    Pull Request Details:
    --------------------
    PR_link: {pr_url}
    Source Branch: {source_branch}
    Target Branch: {target_branch}
    Repository: {repository}
    """

    source_commit = codecommit.get_branch(
        repositoryName=repository, branchName=source_branch
    )["branch"]["commitId"]
    commit_details = []

    def traverse_commits(commit_id):
        if commit_id == merge_base_commit:
            return
        commit = codecommit.get_commit(repositoryName=repository, commitId=commit_id)[
            "commit"
        ]
        author_name = commit["author"].get("name", "Unknown")
        commit_message = commit["message"]
        commit_details.append(f"- {commit_message} (Author): {author_name}")
        for parent_id in commit["parents"]:
            traverse_commits(parent_id)

    traverse_commits(source_commit)

    if commit_details:
        description += (
            "\nCommit Messages and Author(s):\n---------------------------\n"
            + "\n".join(commit_details)
        )
    else:
        description += "\nNo new commits between the source and target branches."
    pyperclip.copy(description)
    return description


# Git commit and push
def git_commit_and_push(commit_message, branch_name):
    try:
        subprocess.run(["git", "commit", "-m", commit_message])
        subprocess.run(["git", "push", "--set-upstream", "origin", branch_name])
        logger.info("Git commit and push successful")
    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {e}")
        sys.exit(1)


# Create Pull Request
def create_pull_request(
    repository, branch_name, target_branch, commit_message, codecommit
):
    logger.info(
        f"Creating pull request from {branch_name} to {target_branch} for {repository}"
    )
    existing_pr = check_existing_pr(repository, branch_name, target_branch, codecommit)
    if existing_pr:
        logger.info(f"Found existing pull request {existing_pr}. Skipping PR creation.")
        return existing_pr
    pr_response = codecommit.create_pull_request(
        title=commit_message,
        targets=[
            {
                "repositoryName": repository,
                "sourceReference": branch_name,
                "destinationReference": target_branch,
            }
        ],
    )
    pr_id = pr_response["pullRequest"]["pullRequestId"]
    logger.info(f"Pull Request Created: {pr_id}")
    return pr_id


# Merge Pull Request
def merge_pull_request(pr_id, repository, codecommit):
    while True:
        try:
            # Retrieve the pull request details
            pr = codecommit.get_pull_request(pullRequestId=pr_id)["pullRequest"]

            # Ensure the pull request has approval rules
            if not pr.get("approvalRules"):
                logger.info("No approval rules found for the pull request.")
                return

            # Retrieve the approval states for the pull request
            approvals = codecommit.get_pull_request_approval_states(
                pullRequestId=pr_id, revisionId=pr["revisionId"]
            )["approvals"]

            # Count the number of approvals
            approval_count = sum(
                1 for approval in approvals if approval["approvalState"] == "APPROVE"
            )

            # Check if the pull request has the required number of approvals
            if approval_count >= 2:
                # Attempt to merge the pull request using fast-forward strategy
                response = codecommit.merge_pull_request_by_fast_forward(
                    pullRequestId=pr_id, repositoryName=repository
                )
                logger.info(f"Merge successful")
                logger.debug(f"Merge successful: {response}")
                break
            else:
                time.sleep(10)
                logger.debug(
                    f"Pull request has {approval_count} approvals; 2 are required. Merge not attempted."
                )
        except ClientError as e:
            logger.error(f"An error occurred: {e}")


# Delete Branch if Merged into dev
def delete_branch_if_needed(branch_name, repository, codecommit):
    if branch_name not in ["dev", "val", "prd"]:
        codecommit.delete_branch(repositoryName=repository, branchName=branch_name)
        logger.info(f"Branch {branch_name} deleted")


def get_latest_pipeline_execution(pipeline_name, client):
    try:
        latest_execution = client.list_pipeline_executions(
            pipelineName=pipeline_name, maxResults=1
        )["pipelineExecutionSummaries"][0]
        return latest_execution["status"]
    except Exception as e:
        logger.error(f"Failed to get latest pipeline execution: {e}")
        raise


def get_pipeline_state(pipeline_name, client):
    try:
        return client.get_pipeline_state(name=pipeline_name)

    except Exception as e:
        logger.error(f"Failed to get pipeline state: {e}")
        raise


# Pipeline status check
def check_pipeline_status(pipeline_name, client):
    logger.info(f"Checking pipeline status for: {pipeline_name}")
    execution_status = get_latest_pipeline_execution(pipeline_name, client)

    while execution_status == "InProgress":
        execution_status = get_latest_pipeline_execution(pipeline_name, client)
        pipeline_state = get_pipeline_state(pipeline_name, client)
        for stage in pipeline_state["stageStates"]:
            if (
                "latestExecution" in stage
                and stage["latestExecution"]["status"] == "InProgress"
            ):
                current_stage = stage["stageName"]
                logger.debug(
                    f"Pipeline is currently running. Current stage: {current_stage}, execution status: {execution_status}"
                )

                for action_state in stage.get("actionStates", []):
                    if action_state.get("actionName") == "approval":
                        token = action_state.get("latestExecution", {}).get("token")
                        if token:
                            try:
                                client.put_approval_result(
                                    pipelineName=pipeline_name,
                                    stageName=current_stage,
                                    actionName="approval",
                                    result={
                                        "summary": "Automatically approved by script.",
                                        "status": "Approved",
                                    },
                                    token=token,
                                )
                                logger.info(
                                    f"Successfully approved the '{current_stage}' stage"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to approve stage '{current_stage}': {e}"
                                )
                                raise
                        break

        time.sleep(10)
    execution_status = get_latest_pipeline_execution(pipeline_name, client)
    logger.info(f"Pipeline execution status: {execution_status}")


# Get pipeline info
def get_pipeline_info(repo_name: str, branch_name: str) -> tuple:
    env = (
        "dev"
        if "dev" in branch_name
        else "val" if "val" in branch_name else "prd" if "prd" in branch_name else None
    )
    if not env:
        raise ValueError("Unsupported branch name")
    pipeline = f"{repo_name}-{env}-cicd"
    aws_env = f"{repo_name}-{env}".replace("infra-", "")
    return env, pipeline, aws_env


# Main execution
if __name__ == "__main__":
    logger = setup_logging()
    commit_message = sys.argv[1] if len(sys.argv) > 1 else "Automated PR"
    target_branch = sys.argv[2] if len(sys.argv) > 2 else "dev"
    repository = os.path.basename(os.getcwd())
    branch_name = get_current_branch()
    # Set up logging and boto3 session
    if len(sys.argv) > 3 and sys.argv[3] == "2":
        pass
    else:

        codecommit, devsecops_session = setup_boto3_session()
        git_commit_and_push(commit_message, branch_name)
        pr_id = create_pull_request(
            repository, branch_name, target_branch, commit_message, codecommit
        )
        pr_url = open_pr_in_chrome(repository, pr_id, devsecops_session)
        pr_description = get_pr_description(pr_id, repository, codecommit)
        logger.info("\nPR Description (copy and paste this to the PR approval person):")
        logger.info(pr_description)
        merge_pull_request(pr_id, repository, codecommit)
        delete_branch_if_needed(branch_name, repository, codecommit)
        time.sleep(10)

    # Pipeline status check
    env, pipeline, aws_env = get_pipeline_info(repository, target_branch)
    session = boto3.Session(profile_name=aws_env)
    client = session.client("codepipeline")
    check_pipeline_status(pipeline, client)
