import json
import time
import os
from twitter.account import Account

def is_rate_limit_error(error):
    """
    Check if an error is a rate limit error (429 Too Many Requests).

    Args:
        error: Exception object or error message

    Returns:
        True if it's a rate limit error, False otherwise
    """
    error_str = str(error).lower()
    # Check for common rate limit indicators
    rate_limit_indicators = [
        '429',
        'too many requests',
        'rate limit',
        'rate_limit',
        'exceeded',
        'quota',
        'limit exceeded'
    ]
    return any(indicator in error_str for indicator in rate_limit_indicators)


def handle_rate_limit_error(error, retry_count, base_wait_time=60):
    """
    Handle rate limit errors with exponential backoff.

    Args:
        error: The exception that occurred
        retry_count: Number of times we've retried
        base_wait_time: Base wait time in seconds (default 60s = 1 minute)

    Returns:
        Wait time in seconds before retrying
    """
    # Exponential backoff: 1min, 2min, 4min, 8min, etc.
    wait_time = base_wait_time * (2 ** retry_count)
    # Cap at 15 minutes (900 seconds)
    wait_time = min(wait_time, 900)

    print(f"\n  ⚠ Rate limit detected (attempt {retry_count + 1})")
    print(f"  ⏳ Waiting {wait_time}s ({wait_time/60:.1f} minutes) before retry...")

    return wait_time


def extract_bookmark_entries_from_response(response_data):
    """
    Extract bookmark entries (tweet IDs and user info) from the response.

    Args:
        response_data: The response data from account.bookmarks()

    Returns:
        List of tuples: [(tweet_id, username), ...]
    """
    bookmark_entries = []
    try:
        # First, check if response is a simple list of tweet IDs or tweet objects
        if isinstance(response_data, list):
            # Check if it's a list of simple values (tweet IDs)
            if len(response_data) > 0 and isinstance(response_data[0], (str, int)):
                # Simple list of tweet IDs
                return [(str(tid), None) for tid in response_data]
            # Check if it's a list of tweet objects
            elif len(response_data) > 0 and isinstance(response_data[0], dict):
                # If it has 'id' or 'id_str' field, it might be a simple tweet object
                if 'id' in response_data[0] or 'id_str' in response_data[0]:
                    result = []
                    for item in response_data:
                        tweet_id = item.get('id_str') or str(item.get('id', ''))
                        username = item.get('user', {}).get('screen_name') if 'user' in item else None
                        if tweet_id:
                            result.append((tweet_id, username))
                    return result
            # Otherwise, treat as GraphQL response structure
            data = response_data[0] if len(response_data) > 0 else {}
        elif isinstance(response_data, dict):
            data = response_data
        else:
            return bookmark_entries

        # Navigate through the nested GraphQL structure (similar to tweets structure)
        timeline = data.get('data', {}).get('bookmark_timeline_v2', {}).get('timeline', {})
        if not timeline:
            # Try alternative path
            timeline = data.get('data', {}).get('user', {}).get('result', {}).get('timeline_v2', {}).get('timeline', {})
        
        instructions = timeline.get('instructions', [])

        for instruction in instructions:
            if instruction.get('type') == 'TimelineAddEntries':
                entries = instruction.get('entries', [])
                for entry in entries:
                    content = entry.get('content', {})
                    # Extract bookmark entries
                    if content.get('entryType') == 'TimelineTimelineItem':
                        item_content = content.get('itemContent', {})
                        if item_content.get('itemType') == 'TimelineTweet':
                            tweet_result = item_content.get('tweet_results', {}).get('result', {})
                            # Get rest_id (the tweet ID)
                            tweet_id = tweet_result.get('rest_id')
                            
                            # Get username from tweet result
                            username = None
                            # Try to get username from user info in tweet
                            user_info = tweet_result.get('core', {}).get('user_results', {}).get('result', {})
                            if user_info:
                                legacy_user = user_info.get('legacy', {})
                                if legacy_user:
                                    username = legacy_user.get('screen_name')
                            
                            if tweet_id:
                                bookmark_entries.append((str(tweet_id), username))

        return bookmark_entries
    except Exception as e:
        print(f"  ⚠ Warning: Error extracting bookmark entries: {e}")
        return bookmark_entries


def extract_all_bookmarks(account, delay_between_requests=2.0):
    """
    Extract all bookmarks from the account with proper rate limit handling.
    Account.bookmarks() returns all bookmarks in a single call.

    Args:
        account: Account instance from twitter.account
        delay_between_requests: Delay in seconds between requests (not used for single call, but kept for consistency)

    Returns:
        List of tuples: [(tweet_id, username), ...] (newest first)
    """
    all_bookmarks = []
    retry_count = 0

    print("Starting to extract bookmarks...")
    print("-" * 50)

    try:
        print("Fetching bookmarks...", end=" ")

        # Fetch all bookmarks (single call, no pagination needed)
        try:
            response_data = account.bookmarks()
            retry_count = 0

        except Exception as e:
            error_msg = str(e)
            print(f"\n  ❌ Error fetching bookmarks: {error_msg}")

            # Check if it's a rate limit error
            if is_rate_limit_error(e):
                wait_time = handle_rate_limit_error(e, retry_count)
                time.sleep(wait_time)
                retry_count += 1
                # Retry the request
                try:
                    response_data = account.bookmarks()
                    retry_count = 0
                except Exception as retry_error:
                    print(f"  ❌ Failed after retry: {retry_error}")
                    raise
            else:
                # For non-rate-limit errors, wait a bit and retry once
                if retry_count < 2:
                    wait_time = delay_between_requests * 3
                    print(f"  ⏳ Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    retry_count += 1
                    try:
                        response_data = account.bookmarks()
                        retry_count = 0
                    except Exception as retry_error:
                        print(f"  ❌ Failed after retry: {retry_error}")
                        raise
                else:
                    print(f"  ❌ Max retries reached. Stopping.")
                    raise

        # Extract bookmark entries from response
        all_bookmarks = extract_bookmark_entries_from_response(response_data)

        if all_bookmarks:
            print(f"✓ Retrieved {len(all_bookmarks)} bookmarks")
        else:
            print("⚠ No bookmarks found")

    except KeyboardInterrupt:
        print("\n\n⚠ Extraction interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Error occurred: {str(e)}")
        raise

    print(f"\n{'='*80}")
    print(f"Bookmark extraction complete!")
    print(f"  Total bookmarks found: {len(all_bookmarks)}")
    print(f"{'='*80}\n")

    return all_bookmarks


def save_bookmarks_and_unbookmark(account, bookmarks, output_file="bookmarks.txt", delay_between_requests=2.0):
    """
    Save bookmark URLs to file (newest first) and unbookmark each one.

    Args:
        account: Account instance from twitter.account
        bookmarks: List of tuples [(tweet_id, username), ...]
        output_file: Output file path
        delay_between_requests: Delay in seconds between unbookmark requests
    """
    print(f"\nSaving bookmarks to {output_file} and unbookmarking...")
    print("-" * 50)

    # Read existing content if file exists
    existing_content = ""
    if os.path.exists(output_file):
        with open(output_file, "r") as f:
            existing_content = f.read()

    # Ask user whether to prepend or append
    while True:
        choice = input("Prepend (p) or append (a) new bookmarks? [p/a]: ").strip().lower()
        if choice in ['p', 'a']:
            break
        print("  ⚠ Invalid choice. Please enter 'p' for prepend or 'a' for append.")

    prepend = (choice == 'p')

    # Collect new bookmark URLs (newest first)
    new_bookmark_urls = []
    unbookmark_count = 0
    retry_count = 0

    # Process bookmarks (they should already be in order, newest first)
    for tweet_id, username in bookmarks:
        # Construct URL
        if username:
            url = f"https://twitter.com/{username}/status/{tweet_id}"
        else:
            # Fallback if username not available
            url = f"https://twitter.com/i/web/status/{tweet_id}"

        # Add to new bookmarks list
        new_bookmark_urls.append(url)

        # Unbookmark the tweet
        try:
            account.unbookmark(tweet_id)
            unbookmark_count += 1
            retry_count = 0  # Reset retry count on success
            
            if unbookmark_count % 10 == 0:
                print(f"  ✓ Processed {unbookmark_count}/{len(bookmarks)} bookmarks...")

        except Exception as e:
            error_msg = str(e)
            print(f"\n  ⚠ Error unbookmarking tweet {tweet_id}: {error_msg}")

            # Check if it's a rate limit error
            if is_rate_limit_error(e):
                wait_time = handle_rate_limit_error(e, retry_count)
                time.sleep(wait_time)
                retry_count += 1
                # Retry the unbookmark
                try:
                    account.unbookmark(tweet_id)
                    unbookmark_count += 1
                    retry_count = 0
                except Exception as retry_error:
                    print(f"  ❌ Failed to unbookmark {tweet_id} after retry: {retry_error}")
            else:
                # For other errors, just log and continue
                if retry_count < 2:
                    wait_time = delay_between_requests * 3
                    print(f"  ⏳ Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    retry_count += 1
                    try:
                        account.unbookmark(tweet_id)
                        unbookmark_count += 1
                        retry_count = 0
                    except Exception as retry_error:
                        print(f"  ❌ Failed to unbookmark {tweet_id} after retry: {retry_error}")
                else:
                    print(f"  ❌ Skipping unbookmark for {tweet_id} after max retries")

        # Rate limiting: wait before next unbookmark request
        if delay_between_requests > 0:
            time.sleep(delay_between_requests)

    # Write bookmarks based on user's choice
    with open(output_file, "w") as f:
        if prepend:
            # Write new bookmarks first (prepended), then existing content
            for url in new_bookmark_urls:
                f.write(f"{url}\n")
            if existing_content:
                f.write(existing_content)
        else:
            # Write existing content first, then new bookmarks (appended)
            if existing_content:
                f.write(existing_content)
            for url in new_bookmark_urls:
                f.write(f"{url}\n")

    print(f"\n{'='*80}")
    print(f"Processing complete!")
    print(f"  Total bookmarks saved: {len(bookmarks)}")
    print(f"  Total unbookmarked: {unbookmark_count}")
    print(f"  Output file: {output_file}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    # Load cookies
    with open("creds.txt", "r") as file:
        cookie_str = file.read().strip()
    cookie_dict = dict(item.split("=", 1) for item in cookie_str.split(";"))

    # Initialize account
    account = Account(cookies=cookie_dict)

    # Configuration
    DELAY_BETWEEN_REQUESTS = 2.0  # Seconds to wait between requests
    OUTPUT_FILE = "bookmarks.txt"

    # Extract all bookmarks
    bookmarks = extract_all_bookmarks(account, delay_between_requests=DELAY_BETWEEN_REQUESTS)

    if bookmarks:
        # Save bookmarks to file and unbookmark them
        save_bookmarks_and_unbookmark(
            account, 
            bookmarks, 
            output_file=OUTPUT_FILE,
            delay_between_requests=DELAY_BETWEEN_REQUESTS
        )
        print(f"\nSuccessfully processed {len(bookmarks)} bookmarks")
    else:
        print("\nNo bookmarks found.")

