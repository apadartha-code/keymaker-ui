#!/usr/bin/env bash

# Exit immediately if a command fails, or if a variable is unset
set -euo pipefail

# 1. Read from the first argument ($1), or fallback to the default path
FIFO_PATH="${1:-/tmp/keymaker_fifo}"

# 2. Check if the path exists at all
if [ ! -e "$FIFO_PATH" ]; then
    echo "Fifo $FIFO_PATH is not available for writing!" >&2
    exit 1
fi

# 3. Test whether the existing path is indeed a FIFO (named pipe)
if [ ! -p "$FIFO_PATH" ]; then
    echo "Error: Path $FIFO_PATH exists but is not a FIFO!" >&2
    exit 1
fi

echo "Using FIFO path: $FIFO_PATH" >&2
echo "Waiting for reader to connect..." >&2

# 4. Open the FIFO for writing via file descriptor 3
# This line BLOCKS until your Python script opens the FIFO for reading
exec 3> "$FIFO_PATH"

# 5. Read the password securely (-s hides the echo input)
# -r prevents backslashes from acting as escape characters
read -rs -p "Enter Secret: " password
echo "" >&2 # Print a newline since 'read -s' does not

# 6. Write the password to the FIFO and close it
echo -n "$password" >&3

# 7. Explicitly close the file descriptor to signal EOF to Python
exec 3>&-

echo "Secret sent successfully." >&2