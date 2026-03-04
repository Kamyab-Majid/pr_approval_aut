import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
import webbrowser

import boto3
import pyjokes
import pyperclip
import requests
from botocore.exceptions import ClientError
from rich.console import Console
from rich.markdown import Markdown


def get_input_with_timeout(prompt, timeout=10, default="yes"):
    def user_input():
        nonlocal user_response
        user_response = input(prompt)

    user_response = None
    thread = threading.Thread(target=user_input)
    thread.daemon = True  # Ensures thread exits if the program stops
    thread.start()
    thread.join(timeout)  # Wait for user input for the specified timeout

    return user_response if user_response is not None else default


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(__name__)


def setup_boto3_session(profile_name="devsecops"):
    session = boto3.Session(profile_name=profile_name)
    return session.client("codecommit"), session


def get_current_branch():
    return (
        subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        .strip()
        .decode("utf-8")
    )


def check_existing_pr(repository, source_ref, destination_ref, codecommit):
    response = codecommit.list_pull_requests(
        repositoryName=repository, pullRequestStatus="OPEN"
    )
    for pr_id in response["pullRequestIds"]:
        pr_details = codecommit.get_pull_request(pullRequestId=pr_id)
        pr = pr_details["pullRequest"]
        print(pr["pullRequestTargets"][0]["sourceReference"])
        if (
            pr["pullRequestTargets"][0]["sourceReference"].replace("refs/heads/", "")
            == source_ref
            and pr["pullRequestTargets"][0]["destinationReference"].replace(
                "refs/heads/", ""
            )
            == destination_ref
        ):
            return pr_id
    return None


def open_pr_in_chrome(repository, pr_id, devsecops_session):
    region = devsecops_session.region_name
    pr_url = f"https://{region}.console.aws.amazon.com/codesuite/codecommit/repositories/{repository}/pull-requests/{pr_id}/details"
    chrome_path = "C:\\Program Files\\Mozilla Firefox\\firefox.exe %s"
    try:
        webbrowser.get(chrome_path).open(pr_url)
        logger.info(f"Opened PR in Chrome: {pr_url}")
        return pr_url
    except Exception as e:
        print(e)
        logger.error(f"Failed to open Chrome: {e}")
        logger.info(f"PR URL: {pr_url}")


def get_pr_description(pr_id, repository, codecommit):
    pr_details = codecommit.get_pull_request(pullRequestId=pr_id)
    pr = pr_details["pullRequest"]
    source_branch = pr["pullRequestTargets"][0]["sourceReference"].replace(
        "refs/heads/", ""
    )
    print(pr["pullRequestTargets"][0]["sourceReference"])
    target_branch = pr["pullRequestTargets"][0]["destinationReference"].replace(
        "refs/heads/", ""
    )
    merge_base_commit = pr["pullRequestTargets"][0]["mergeBase"]
    description = f"""
--------------------------------------------
{pr_url}
from `{source_branch}` branch to `{target_branch}` branch in  `{repository}` repository
commit(s)_details:
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
        if author_name != "Kamyab":
            commit_details.append(f"- {commit_message} by {author_name}")
        else:
            commit_details.append(f"- {commit_message}")
        for parent_id in commit["parents"]:
            traverse_commits(parent_id)

    traverse_commits(source_commit)

    if commit_details:
        try:
            description += "\n".join(commit_details)
        except TypeError as e:
            logger.error(f"Failed to add {commit_details}: {e}")
    else:
        description += "\nNo new commits between the source and target branches."

    # Append a random quote or joke
    description += """"\n------------------------------"""

    pyperclip.copy(description)
    return description


def get_thought_data(url):
    # Send GET request
    response = requests.get(url)
    data = response.json()

    # Check if response contains expected data
    if "thought" in data:
        thought_data = data["thought"]

        # Print the recent quote from Thought

        # Print quotes from related authors
        related_author_thoughts = thought_data.get("relatedAuthorThoughts", [])
        for author_thought in related_author_thoughts:
            # print(author_thought.get("quote", "No quote available"))
            pass
        # Print quotes from related theme thoughts
        related_theme_thoughts = thought_data.get("relatedThemeThoughts", [])
        for theme_thought in related_theme_thoughts:
            return theme_thought.get("quote", "No quote available")
    else:
        print("No 'thought' data found in the response.")


def get_random_quote_or_joke():
    # Define pyjokes categories
    pyjokes_categories = ["all"]

    # Define pyquotegen categories
    pyquotegen_categories = [
        "inspirational",
        "motivational",
        "funny",
        "life",
        "friendship",
        "success",
    ]

    # Randomly choose between pyjokes and pyquotegen
    if random.choice([True, False]):
        if random.choice([True, False]):
            get_thought_data(
                "http://www.forbes.com/forbesapi/thought/uri.json?enrich=true&query=1&relatedlimit=5"
            )
        # Select a random category from pyjokes
        category = random.choice(pyjokes_categories)
        return pyjokes.get_joke(category=category)
    return None


logger = logging.getLogger(__name__)


def branch_has_diverged(target_branch):
    # Fetch latest refs for comparison
    subprocess.run(["git", "fetch", "origin", target_branch], check=True)

    # Get current branch name
    current_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # Commits ahead of target_branch
    commits_ahead = subprocess.run(
        ["git", "rev-list", f"{target_branch}..{current_branch}", "--count"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Commits behind target_branch
    commits_behind = subprocess.run(
        ["git", "rev-list", f"{current_branch}..{target_branch}", "--count"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    commits_ahead = int(commits_ahead)
    commits_behind = int(commits_behind)

    return commits_ahead > 1 or commits_behind > 0


def git_commit_and_push(commit_message, branch_name):
    try:
        # Get the current branch name

        current_branch = (
            subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
            .strip()
            .decode("utf-8")
        )
        print("_____________________________________")
        print(current_branch, target_branch)
        print("_____________________________________")
        # Switch to target branch and pull latest changes
        subprocess.run(["git", "commit", "-m", commit_message])

        subprocess.run(["git", "switch", target_branch], check=True)
        print(f"Switched to branch {branch_name}")
        subprocess.run(["git", "pull"], check=True)
        print(f"Pulled latest changes from {branch_name}")

        # Switch back to the original branch
        subprocess.run(["git", "switch", current_branch], check=True)

        # Rebase the current branch with the target branch interactively
        if target_branch == "dev" and branch_has_diverged(target_branch):
            subprocess.run(["git", "rebase", target_branch, "-i"], check=True)

        # Commit and push changes
        subprocess.run(
            ["git", "push", "--force", "--set-upstream", "origin", current_branch]
        )

        logger.info("Git commit and push successful")

    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {e}")
        sys.exit(1)


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
        title=commit_message[0:50],
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


def merge_pull_request(pr_id, repository, codecommit):
    i = 0
    while True:
        try:
            # Retrieve the pull request details
            pr = codecommit.get_pull_request(pullRequestId=pr_id)["pullRequest"]

            # Retrieve the approval states for the pull request if approval rules exist
            if pr.get("approvalRules"):
                _ = codecommit.get_pull_request_approval_states(
                    pullRequestId=pr_id, revisionId=pr["revisionId"]
                )["approvals"]

            # Count the number of approvals
            # approval_count = sum(
            #     1 for approval in approvals if approval["approvalState"] == "APPROVE"
            # )

            # approval_authors = [
            #     approval["userArn"]
            #     for approval in approvals
            #     if approval["approvalState"] == "APPROVE"
            # ]
            # pr_description = codecommit.get_pull_request(pullRequestId=pr_id)[
            #     "pullRequest"
            # ]["description"]
            # print(f"pull request description: {pr_description}")
            response = codecommit.merge_pull_request_by_fast_forward(
                pullRequestId=pr_id, repositoryName=repository
            )
            logger.info("Merge successful")
            console = Console()
            try:
                console.print(Markdown(response["pullRequest"]["description"]))
            except KeyError as e:
                logger.error(f"Failed to print pull request description: {e}")
            break

        except ClientError as e:
            time.sleep(60)
            i += 1
            if i > 10:
                logger.error(f"An error occurred: {e}")
                i = 0


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


def get_predeploy_execution_id(pipeline_name, client, codebuild_client):
    """Get the CodeBuild execution ID of the predeploy stage from the latest pipeline execution."""
    pipeline_state = client.get_pipeline_state(name=pipeline_name)
    for stage in pipeline_state["stageStates"]:
        if (
            "latestExecution" in stage
            and stage["latestExecution"]["status"] == "InProgress"
        ):
            for action_state in stage.get("actionStates", []):
                if action_state.get("actionName") == "predeploy":
                    execution_details = action_state.get("latestExecution", {})
                    external_execution_id = execution_details.get("externalExecutionId")
                    if external_execution_id:
                        return external_execution_id
    return None


def stream_codebuild_logs(build_id, codebuild_client, logs_client, stop_event):
    """Stream CodeBuild logs in real-time until stop_event is set."""
    next_token = None
    log_group_name = None
    log_stream_name = None
    
    while not stop_event.is_set():
        try:
            # Get build info to find log location
            if not log_group_name or not log_stream_name:
                build_info = codebuild_client.batch_get_builds(ids=[build_id])
                if build_info["builds"]:
                    logs_info = build_info["builds"][0].get("logs", {})
                    log_group_name = logs_info.get("groupName")
                    log_stream_name = logs_info.get("streamName")
                    
                    if not log_group_name or not log_stream_name:
                        time.sleep(2)
                        continue
            
            # Fetch log events
            kwargs = {
                "logGroupName": log_group_name,
                "logStreamName": log_stream_name,
                "startFromHead": True if next_token is None else False,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            
            response = logs_client.get_log_events(**kwargs)
            
            for event in response.get("events", []):
                message = event.get("message", "").rstrip()
                if message:
                    print(f"  {message}")
            
            new_token = response.get("nextForwardToken")
            if new_token == next_token:
                # No new logs, wait a bit
                time.sleep(2)
            else:
                next_token = new_token
                
        except logs_client.exceptions.ResourceNotFoundException:
            # Log stream not created yet
            time.sleep(2)
        except Exception as e:
            logger.debug(f"Log streaming error: {e}")
            time.sleep(2)


def get_codebuild_timing(build_id, codebuild_client):
    """Get timing breakdown for a CodeBuild execution."""
    try:
        build_info = codebuild_client.batch_get_builds(ids=[build_id])
        if not build_info["builds"]:
            return None
        
        build = build_info["builds"][0]
        phases = build.get("phases", [])
        
        timing_info = {
            "build_status": build.get("buildStatus", "UNKNOWN"),
            "phases": {}
        }
        
        for phase in phases:
            phase_type = phase.get("phaseType", "UNKNOWN")
            duration = phase.get("durationInSeconds")
            if duration is not None:
                timing_info["phases"][phase_type] = duration
        
        # Calculate totals
        queued_time = timing_info["phases"].get("QUEUED", 0)
        provisioning_time = timing_info["phases"].get("PROVISIONING", 0)
        download_source_time = timing_info["phases"].get("DOWNLOAD_SOURCE", 0)
        install_time = timing_info["phases"].get("INSTALL", 0)
        pre_build_time = timing_info["phases"].get("PRE_BUILD", 0)
        build_time = timing_info["phases"].get("BUILD", 0)
        post_build_time = timing_info["phases"].get("POST_BUILD", 0)
        upload_artifacts_time = timing_info["phases"].get("UPLOAD_ARTIFACTS", 0)
        finalizing_time = timing_info["phases"].get("FINALIZING", 0)
        
        timing_info["summary"] = {
            "queued": queued_time,
            "provisioning": provisioning_time,
            "download_source": download_source_time,
            "install": install_time,
            "pre_build": pre_build_time,
            "build": build_time,
            "post_build": post_build_time,
            "upload_artifacts": upload_artifacts_time,
            "finalizing": finalizing_time,
            "total": sum([queued_time, provisioning_time, download_source_time, 
                         install_time, pre_build_time, build_time, post_build_time,
                         upload_artifacts_time, finalizing_time])
        }
        
        return timing_info
    except Exception as e:
        logger.error(f"Failed to get CodeBuild timing: {e}")
        return None


def print_codebuild_timing(timing_info, action_name="CodeBuild"):
    """Print formatted timing information for a CodeBuild execution."""
    if not timing_info:
        return
    
    summary = timing_info.get("summary", {})
    status = timing_info.get("build_status", "UNKNOWN")
    
    print(f"\n{'='*60}")
    print(f"  {action_name} Timing Summary (Status: {status})")
    print(f"{'='*60}")
    
    def format_time(seconds):
        if seconds >= 60:
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins}m {secs}s"
        return f"{seconds}s"
    
    if summary.get("queued", 0) > 0:
        print(f"  Queued:           {format_time(summary['queued'])}")
    if summary.get("provisioning", 0) > 0:
        print(f"  Provisioning:     {format_time(summary['provisioning'])}")
    if summary.get("download_source", 0) > 0:
        print(f"  Download Source:  {format_time(summary['download_source'])}")
    if summary.get("install", 0) > 0:
        print(f"  Install:          {format_time(summary['install'])}")
    if summary.get("pre_build", 0) > 0:
        print(f"  Pre-Build:        {format_time(summary['pre_build'])}")
    if summary.get("build", 0) > 0:
        print(f"  Build:            {format_time(summary['build'])}")
    if summary.get("post_build", 0) > 0:
        print(f"  Post-Build:       {format_time(summary['post_build'])}")
    if summary.get("upload_artifacts", 0) > 0:
        print(f"  Upload Artifacts: {format_time(summary['upload_artifacts'])}")
    if summary.get("finalizing", 0) > 0:
        print(f"  Finalizing:       {format_time(summary['finalizing'])}")
    
    print(f"  {'-'*40}")
    print(f"  Total:            {format_time(summary.get('total', 0))}")
    print(f"{'='*60}\n")


def check_pipeline_status(pipeline_name, client, codebuild_client, logs_client):
    logger.info(f"Checking pipeline status for: {pipeline_name}")
    execution_status = get_latest_pipeline_execution(pipeline_name, client)
    external_execution_id = None
    current_streaming_build_id = None
    stop_event = None
    log_thread = None
    completed_builds = set()  # Track builds we've already shown timing for
    i = 0
    
    while execution_status == "InProgress":
        execution_status = get_latest_pipeline_execution(pipeline_name, client)
        pipeline_state = get_pipeline_state(pipeline_name, client)
        
        for stage in pipeline_state["stageStates"]:
            if (
                "latestExecution" in stage
                and stage["latestExecution"]["status"] == "InProgress"
            ):
                current_stage = stage["stageName"]

                for action_state in stage.get("actionStates", []):
                    action_name = action_state.get("actionName", "")
                    latest_exec = action_state.get("latestExecution", {})
                    action_exec_id = latest_exec.get("externalExecutionId")
                    action_status = latest_exec.get("status")
                    
                    # Track CodeBuild actions (predeploy, deploy, etc.)
                    if action_exec_id and action_name != "approval":
                        # Check if this build just completed and we haven't shown timing yet
                        if action_status in ["Succeeded", "Failed"] and action_exec_id not in completed_builds:
                            # Stop streaming if we were streaming this build
                            if current_streaming_build_id == action_exec_id and stop_event:
                                stop_event.set()
                                if log_thread:
                                    log_thread.join(timeout=2)
                                current_streaming_build_id = None
                            
                            # Show timing for completed build
                            timing_info = get_codebuild_timing(action_exec_id, codebuild_client)
                            print_codebuild_timing(timing_info, action_name)
                            completed_builds.add(action_exec_id)
                        
                        # Start streaming logs for new in-progress build
                        elif action_status == "InProgress" and action_exec_id != current_streaming_build_id:
                            # Stop previous stream if any
                            if stop_event:
                                stop_event.set()
                                if log_thread:
                                    log_thread.join(timeout=2)
                            
                            # Start new stream
                            logger.info(f"Streaming logs for {action_name} ({action_exec_id})...")
                            print(f"\n{'='*60}")
                            print(f"  {action_name} - Live Logs")
                            print(f"{'='*60}")
                            
                            stop_event = threading.Event()
                            log_thread = threading.Thread(
                                target=stream_codebuild_logs,
                                args=(action_exec_id, codebuild_client, logs_client, stop_event),
                                daemon=True
                            )
                            log_thread.start()
                            current_streaming_build_id = action_exec_id
                        
                        # Keep track of predeploy for approval logic
                        if action_name == "predeploy":
                            external_execution_id = action_exec_id
                    
                    # Handle approval action
                    if action_name == "approval" or action_name == "ApproveDeploy":
                        token = latest_exec.get("token")
                        if token:
                            # Stop streaming before approval prompt
                            if stop_event:
                                stop_event.set()
                                if log_thread:
                                    log_thread.join(timeout=2)
                                current_streaming_build_id = None
                            
                            # Show predeploy timing if available
                            if external_execution_id and external_execution_id not in completed_builds:
                                timing_info = get_codebuild_timing(external_execution_id, codebuild_client)
                                print_codebuild_timing(timing_info, "predeploy")
                                completed_builds.add(external_execution_id)
                            
                            # Show CDK diff output
                            if external_execution_id:
                                build_info = codebuild_client.batch_get_builds(
                                    ids=[external_execution_id]
                                )
                                logs_info = build_info["builds"][0]["logs"]
                                log_group_name = logs_info["groupName"]
                                log_stream_name = logs_info["streamName"]

                                log_events = logs_client.get_log_events(
                                    logGroupName=log_group_name,
                                    logStreamName=log_stream_name,
                                    startFromHead=True,
                                )

                                output_message = ""
                                for event in log_events["events"]:
                                    output_message += event["message"]
                                match = re.search(
                                    r"_________________result_________________(.*?)_________________result_________________",
                                    output_message,
                                    re.DOTALL,
                                )
                                if match:
                                    extracted_text = match.group(1).strip()
                                    pattern = (
                                        r"^(start: Building.*|success: Built.*)$"
                                    )
                                    cleaned_text = re.sub(
                                        pattern,
                                        "",
                                        extracted_text,
                                        flags=re.MULTILINE,
                                    ).strip()
                                    pattern = r"^(start: (Building|Publishing) .+)$|^(success: (Built|Published) .+)$"
                                    cleaned_text = re.sub(
                                        pattern,
                                        "",
                                        cleaned_text,
                                        flags=re.MULTILINE,
                                    ).strip()
                                    cleaned_text = cleaned_text.split(
                                        "### Stacks without changes:"
                                    )[0]
                                    cleaned_text = re.sub(
                                        r"\n\n+", "\n", cleaned_text
                                    )
                                    print(cleaned_text)
                            
                            input_prompt = get_input_with_timeout(
                                f"Approve '{current_stage}' stage? (yes/no): ",
                                timeout=5,
                            )
                            if input_prompt != "yes":
                                continue
                            try:
                                client.put_approval_result(
                                    pipelineName=pipeline_name,
                                    stageName=current_stage,
                                    actionName=(
                                        "approval"
                                        if pipeline_name != "infra-devsecops-cicd"
                                        else "ApproveDeploy"
                                    ),
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
        i += 1
        if i > 10:
            logger.info(
                f"Pipeline is currently running. Current stage: {current_stage}, execution status: {execution_status}"
            )
            i = 0
    
    # Stop any remaining log stream
    if stop_event:
        stop_event.set()
        if log_thread:
            log_thread.join(timeout=2)
    
    # Show timing for any remaining builds that completed
    pipeline_state = get_pipeline_state(pipeline_name, client)
    for stage in pipeline_state["stageStates"]:
        for action_state in stage.get("actionStates", []):
            action_name = action_state.get("actionName", "")
            latest_exec = action_state.get("latestExecution", {})
            action_exec_id = latest_exec.get("externalExecutionId")
            if action_exec_id and action_exec_id not in completed_builds and action_name != "approval":
                timing_info = get_codebuild_timing(action_exec_id, codebuild_client)
                print_codebuild_timing(timing_info, action_name)
                completed_builds.add(action_exec_id)
    
    execution_status = get_latest_pipeline_execution(pipeline_name, client)
    logger.info(f"Pipeline execution status: {execution_status}")


def get_pipeline_info(repo_name: str, branch_name: str) -> tuple:
    env = (
        "dev"
        if "dev" in branch_name
        else (
            "val"
            if "val" in branch_name
            else (
                "prd"
                if "prd" in branch_name
                else "prd" if "main" in branch_name else None
            )
        )
    )
    if branch_name == "test":
        env = "dev"

    if not env:
        raise ValueError("Unsupported branch name")
    pipeline = f"{repo_name}-{env}-cicd"
    aws_env = f"{repo_name}-{env}".replace("infra-", "")
    if aws_env == "devsecops-cicd-prd":
        aws_env = "devsecops"
        pipeline = "infra-devsecops-cicd"
    if branch_name == "test":
        pipeline = f"{repo_name}-{branch_name}-cicd"

    return env, pipeline, aws_env


if __name__ == "__main__":
    external_execution_id = None
    # do a cdk diff first

    logger = setup_logging()

    target_branch = sys.argv[2] if len(sys.argv) > 2 else "dev"
    repository = os.path.basename(os.getcwd())
    branch_name = get_current_branch()
    commit_message = sys.argv[1] if len(sys.argv) > 1 else branch_name
    commit_message = branch_name.replace("-", " ") + ": " + commit_message
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
        logger.info(pr_description)
        if (
            repository != "infra-devsecops-cicd"
            and repository != "infra-cdk-playbook"
            and target_branch == "dev"
        ):
            subprocess.run(["npx", "cdk", "synth"], shell=True, check=False)
        merge_pull_request(pr_id, repository, codecommit)
        delete_branch_if_needed(branch_name, repository, codecommit)
        time.sleep(20)
    env, pipeline, aws_env = get_pipeline_info(repository, target_branch)
    session = boto3.Session(profile_name=aws_env)
    codebuild_client = session.client("codebuild")
    client = session.client("codepipeline")
    logs_client = session.client("logs")

    check_pipeline_status(pipeline, client, codebuild_client, logs_client)
