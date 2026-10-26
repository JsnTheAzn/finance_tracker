Claude code etiquette:
Specify what code libraries Claude can use for the project, because Claude likes to bring in other outside libraries, which can be hard to understand
Always be sure to include a "Please document your process of what you did, and describe the steps you took to code this. As well, include extensive code documentation and give detailed code comments." at then end of your prompts

# Phase 1
We are going to develop the Backend logic of this finance tracker. We will be using Python + Fast API where we will connect to API's and banks using Plaid but don't worry about that for now. Let's define a transaction.

## Transaction
I want to create a transaction.py class where it represents a single transaction. A transaction should hold an ID, amount, description, category, and date. Also it needs to have a UUID to uniquely identify each transaction. Have the money amount be a signed integer and allow the attributes to be accessed after the object is created. 

Please use the Pydantic library to define the what the API expects.
amount = int
description = str
category = str
date = date. 

## Transaction manager
I also want a transaction_manager.py where it will manage the transactions. I want to be able to add transactions, retrieve transactions by ID, delete transactions, and a way to calculate the net change across all transactions. 

I want to use a Dictionary as my data structure to hold all the Transactions and hash every ID, creating a UUID.


Create a main.py hat instantiates your classes, adds three transactions, retrieves one by UUID, calculates the net change, and deletes one. You can use print statements to verify the results.

[] Change transaction_manager to connect to a DB (Do not implement this yet)
[] Complete

# Phase 2
This phase I want to incorportate FastAPI. This will be the way where we will have the Frontend, backed and Plaid connections to talk to each other. 

In main.py, please have fastAPI actions called getTransaction, addTransaction, deleteTransaction, getAllTransaction. This will be our basic API Calling functions for now. I will add more once we get going.

[x] Added basic API functions

[] Complete

# Phase 3
This phase we will now incorportate a Plaid sandbox environment to ensure everything is working properly with fake accounts. We will later incorporate a real account later but for now it's a sandbox environment. 

I want to now createa a plaid_services.py in the backend to help connect a simulated sandbox enviornment from plaid. 
Create a simulated bank connection using Plaid Sandbox.
Exchange the Sandbox public token for an access token.
Retrieve transactions using Plaid's /transactions/sync endpoint.
Support pagination using next_cursor and has_more.
Display a sanitized summary of the transactions for local testing.
Handle API failures without exposing credentials or access tokens.
Do not integrate real bank accounts yet.

Do not rewrite existing application components unnecessarily.
Keep Plaid integration separate from FastAPI routing.
Do not implement React or PostgreSQL yet.
Do not hardcode credentials or commit them to GitHub.
Do not expose Sandbox access tokens through public API endpoints.
Follow the current official Plaid API documentation.

[x] Complete

# Phase 4
This phase we will connect the Plaid transactions to our own TransactionManager so the synced data actually lives in the app, and syncing more than once does not create duplicates.

Add an optional external_id (str or None) to the Transaction model. This holds Plaid's transaction_id so we know which Plaid transaction each of ours came from.
In plaid_services.py, have to_transaction set external_id.
In transaction_manager.py, keep a second dictionary that maps external_id to our UUID so we can find a Plaid transaction quickly.
Add an apply_sync(added, modified, removed) method to TransactionManager:
    added -> store new transactions (skip/update if the external_id already exists)
    modified -> update the existing transaction with the same external_id
    removed -> delete the transaction with that external_id
Have the plaid_services demo load the synced transactions into a TransactionManager and print the count and net change.

Do not implement PostgreSQL, React, or new FastAPI routes yet.
Do not change the existing API endpoints.
Libraries allowed: Pydantic, httpx, FastAPI and the Python standard library only.
Write tests showing that syncing the same data twice adds 0 new transactions, and that modified and removed are applied.

Please document your process of what you did, and describe the steps you took to code this. As well, include extensive code documentation and give detailed code comments.

[] Complete



Throughout all phases, please run tests and make sure it works properly. 
