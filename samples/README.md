# Samples

Copy both files into `outbox/` to test the approval flow end to end:

- `00000001-sample-approve.json`: tap **Post to channel**. It should appear in the channel, and the bot confirms.
- `00000002-sample-skip.json`: tap **Skip**. The bot confirms, and nothing reaches the channel.

Delete the test post from the channel afterwards.
