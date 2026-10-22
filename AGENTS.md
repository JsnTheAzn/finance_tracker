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



[] Complete


Throughout all phases, please run tests and make sure it works properly. 