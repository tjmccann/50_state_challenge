from flask import Flask, request, redirect
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from oauth2client.service_account import ServiceAccountCredentials
from state_data import states, states_by_num, countries
from datetime import date
import gspread
from secret import account_sid, auth_token

# Session States:
# 0 = no open session
# 1 = state entered; prompting for brewery
# 2 = brewery entered; prompting for beer
# 4 = new user asking to join; prompting for name
# 5 = new user entered name; prompting for email  [NOT CURRENTLY IN USE]

#Define a custom class for all contest participants
class User:
    def __init__(self, index, name, mobile, home_col):
        self.index = index
        self.name = name
        self.mobile = mobile
        self.home_col = home_col
        self.session_state = 0
        pending_add_state = 0
        pending_add_brewery = ''
        pending_add_beer = ''

class New_User:
    def __init__(self, mobile):
		self.mobile = mobile
		self.name = ''
		self.email = ''
		self.session_state = 4


# Twilio API credentials from from twilio.com/console
tw_client = Client(account_sid, auth_token)

'''
# THIS DATA POINTS TO THE **LIVE** APP SMS NUMBER AND DATA SPREADSHEETS
# UNCOMMENT THIS SECTION (AND COMMENT NEXT SECTION) WHEN PUSHING TO PRODUCTION
# ------------------------------------------
data_sheet = ("50_state_data")
mainsheet = ("First Annual 50 State Challenge")
app_phnum = "+14159802337" 
# ------------------------------------------
'''


# THIS DATA POINTS TO THE **TEST** APP SMS NUMBER AND DATA SPREADSHEETS
# UNCOMMENT THIS SECTION (AND COMMENT PREVIOUS SECTION) WHEN BUILDING/TESTING
# ------------------------------------------
data_sheet = ("TEST VERSION - 50_state_data")
mainsheet = ("TEST VERSION - 50 State Challenge") 
app_phnum = "+12028398684"
# ------------------------------------------


# Define the string a user will be sent if they text "OPTIONS"
help_string = """To enter a new beer, text the state name or abbreviation).\n
While entering a new beer, send RESET to start over.\n
To view the latest standings, send LEADERBOARD.\n
To view all of your completed states, send MY STATES.\n
If you know someone who wants to join, have them text I WANT IN to 415-980-BEER (2337)."""

# Google Drive API for connecting to spreadsheets
def g_connect(sheet_name):
	scope = ['https://spreadsheets.google.com/feeds']
	creds = ServiceAccountCredentials.from_json_keyfile_name('client_secret.json', scope)
	g_client = gspread.authorize(creds)
	sheet = g_client.open(sheet_name).sheet1
	return sheet

# Build initial dataset of users, pulling data from the secondary Google doc
def build_users(doc):
	sheet = g_connect(doc)
	total_users = int(sheet.cell(1,7).value)
	i = 2
	while i < (total_users + 2):
		index = int(sheet.cell(i,1).value)
		name = sheet.cell(i,2).value
		mobile = sheet.cell(i,3).value
		home_col = int(sheet.cell(i,4).value)
		new_user = User(index, name, mobile, home_col)
		users.append(new_user)
		i += 1

#Webhook to listen for incoming SMS
app = Flask(__name__)
@app.route("/sms", methods=['GET', 'POST'])
def incoming_sms():
	body = request.values.get('Body', None)
	from_num = request.values.get('From', None)
	known_user = verify_incoming(from_num, users)  #check if the incoming SMS is from an existing user
	new_user = verify_incoming(from_num, new_users) #check if the incoming SMS is from someone in the midst of signup flow
	if known_user:		
		process_convo(known_user, body)		#if the SMS is from an existing user, send the context of the SMS to the main process_convo function
	elif new_user:
		if new_user.session_state == 4:		#if the SMS is from somone who previously texted "I want in", this SMS should be their name.
			new_user.name = body
			new_user.session_state = 5
			add_user(new_user)
#			send_text(new_user.mobile, "Please enter your gmail address so you can be added to the Google Doc")
#		elif new_user.session_state == 5:
#			add_user(new_user, body)
		else:
			print "Error!!!"
	elif body.lower() == 'i want in':	#if it's neither an existing or in-midst-of-joining user, check for the "i want in" command
		new_user = New_User(from_num)	#create a New_user instance to store the inbound phone number
		new_users.append(new_user)		#add the instance to the list of pending new users
		send_text(from_num, "Enter your name:")
		print "Phone num added to 'new users' list.  Prompted to send name."  
	else:	# Otherwise, it's an unrecognized number and ask if they want to join.
		send_text(from_num, "Your phone number isn't recognized.  Sorry. Reply with 'I WANT IN' to join.")
		print "LOG:  Unrecognized phone number.  Prompted to join."
	return "processed"


# When webhook detects new incoming text, check if its a known user.  If so, return that user.
def verify_incoming(num, list):
	for u in list:	
		if u.mobile == num:
			return u
		else:
			continue
	return None

#Text has been verified as coming from a known user, so check the session status of that user and proceed accordingly.
def process_convo(user, text):
	if text.lower() == 'options':
		send_text(user.mobile, help_string)
	elif text.lower() == 'leaderboard':
		text_string = leaderboard(mainsheet)
		send_text(user.mobile, text_string)
	elif text.lower() == 'my states':
		text_string = my_states(user, mainsheet)
		send_text(user.mobile, text_string)
	elif text.lower() == 'reset':
		user.session_state = 0
		text_string = "Starting over.  To add a new beer, enter a state name or abbreviation."
		send_text(user.mobile, text_string)		
	elif user.session_state == 0:
		#verify the contents of the text is a state, and if so return the state's gsheet row number.
		state = is_state(text)
		if state:
			state_beer = current_beer(user, state)
			#if the user already has a beer from that state, tell them
			if state_beer:
				text_string = "You've already entered a beer from %s:  %s by %s on %s." %(states_by_num[state], state_beer[0], state_beer[1], state_beer[2])
				send_text(user.mobile, text_string)
			else:
				user.session_state=1
				user.pending_add_state = state
				text_string = "Adding a new beer from %s. (If this is an error, reply with RESET to start over.) \n\nEnter the name of the BREWERY:" %(states_by_num[state])
				send_text(user.mobile, text_string)
				print "LOG:  User '%s' entered a state (%s).  Have asked for a brewery name.  Waiting." %(user.name, states_by_num[state])
		else:
			country = is_country(text)
			if country:
				text_string = "Imports are for assholes.  Drink an American beer next time, dick."
				send_text(user.mobile, text_string)
				send_text("+15126367011", "%s just drank an import from %s." %(user.name, country))
			else:
				text_string = "Input not recognized.  Please enter a state or reply with OPTIONS for help."
				send_text(user.mobile, text_string)			
	elif user.session_state == 1:  #User has already entered a state, next step is to ask for brewery name.
		user.pending_add_brewery = text
		user.session_state = 2
		text_string = "Enter the name of the BEER from %s you want to add:" %(user.pending_add_brewery)
		send_text(user.mobile, text_string)
		print "LOG:  User '%s' entered a brewery (%s).  Asked for a beer name.  Waiting." %(user.name, user.pending_add_brewery)
	elif user.session_state == 2:    #User has already entered a state and brewery, last step is to ask for beer name.
		user.pending_add_beer = text
		remaining = add_to_gsheet(user)
		text_string = "Got it.  You've checked %s off your list. You have %s states to go." %(states_by_num[user.pending_add_state], remaining)
		send_text(user.mobile, text_string)
		print "LOG:  New beer added to spreadsheet.  User: '%s'  State: %s  |  Brewery: %s  |  Beer: %s" %(user.name, states_by_num[user.pending_add_state], user.pending_add_brewery, user.pending_add_beer)
		user.session_state = 0
		user.pending_add_state = ''
		user.pending_add_brewery = ''
		user.pending_add_beer = ''
		print "LOG:  Resetting user session to rest state."

#Function for adding a new user (who has entered info duing signup flow) to the spreadsheet and the app's internal data list
def add_user(user):  #change to add_user(user, email) if you later decide to also prompt for email
	scope = ['https://spreadsheets.google.com/feeds']
	creds = ServiceAccountCredentials.from_json_keyfile_name('client_secret.json', scope)
	g_client = gspread.authorize(creds)
	doc = g_client.open(mainsheet)
	msheet = doc.sheet1
	dsheet = g_connect(data_sheet)
	curr = int(dsheet.cell(1,7).value)
	drow = curr+2
	hcol = (curr+1)*3
	mob = "'"+user.mobile
	dsheet.update_cell(drow,1, curr)		#fill in index column on data sheet
	dsheet.update_cell(drow,2, user.name)	#fill in name column on data sheet	
	dsheet.update_cell(drow,3, mob)			#fill in name column on data sheet
	dsheet.update_cell(drow,4, hcol)		#fill in home_column column on data sheet
	msheet.update_cell(1, hcol, user.name)	#add the user's name to the header on the main sheet
	new_user = User(curr, user.name, user.mobile, hcol)  #save the user's data in a User record, and...
	users.append(new_user)					# ... add the new record to the list of users.
	send_text(user.mobile, "Welcome to the 50 State Beer Challenge.  To enter a beer, reply with a state name or abbrev.  Reply OPTIONS for more commands.")
	send_text("+15126367011", "Your friend %s has joined the beer challenge."%(user.name))
	print "LOG:  New user %s added to the challenge at phone number %s)" %(user.name, user.mobile)
	new_users.remove(user)
#open the full main spreasheet and add email permission
'''	doc.share(email, perm_type='user', role='writer', notify=True, email_message="You've joined the 50 State Challenge.  Here's the Google Doc link.")
	print "LOG:  New user %s has been invited to the google doc at email address %s)" %(user.name, email)
	print new_users'''

# Checks whether a user already has a beer entered for a given state.  Returns the beer info if so.
def current_beer(user, row):
	sheet = g_connect(mainsheet)
	r = row
	c = user.home_col
	brewery = sheet.cell(r,c).value
	beer = sheet.cell(r,c+1).value
	date = sheet.cell(r,c+2).value
	if brewery:
		print "You drank a %s from %s on %s" %(beer, brewery, date)
		return (beer, brewery, date)
	else:
		return None

# Basic utility function for sending SMS messages (given a mobile number and a text string) 
def send_text(num, string):
	tw_client.messages.create(
	    to=num, 
	    from_=app_phnum,
	    body=string)

# Validate the contents of the inbound text against dictionary of state names & abbrevs.
# Return that state's gsheet row number.
def is_state(text):
	for s in states:
		if s.lower() == text.lower():
			return (states[s])
	return None

def is_country(text):
	for c in countries:
		if c.lower() == text.lower():
			return (text)
	return None

# Write data to the spreadsheet after user has finished a new entry.
def add_to_gsheet(data):
	r = data.pending_add_state
	c = data.home_col
	brewery = data.pending_add_brewery
	beer = data.pending_add_beer
	datestamp = date.today()
	sheet = g_connect(mainsheet)
	sheet.update_cell(r,c, brewery)
	sheet.update_cell(r,c+1, beer)
	sheet.update_cell(r,c+2, datestamp)
	return sheet.cell(1, c+2).value

# Build a "leaderboard" list from gsheet "doc" of all users and their current states remaining.  Return output string for SMS.
def leaderboard(doc):
	sheet = g_connect(doc)
	i = 0
	scores = []
	while i < len(users):
		c = users[i].home_col
		score_tuple = (users[i].name, int(sheet.cell(1, c+2).value))
		scores.append(score_tuple)
		i += 1
	sorted_scores = sorted(scores, key=lambda x: x[1])
	output = ["LEADERBOARD:\n"]
	place = 1
	for item in sorted_scores:
		out_string = "%i)  %s:  %i down, %i to go.\n" %(place, item[0], 51-item[1], item[1])
		output.append(out_string)
		place += 1
	print "LOG:  Leaderboard request handled and sent"
	return "".join(output)

#Pull a list of all states a user has completed.  Return output string.
def my_states(user, doc):
	sheet = g_connect(doc)
	col = sheet.col_values(user.home_col)
	i=2
	output = "Your completed states:"
	while i < 53:
		if col[i] != "":
			output = output + "\n" + states_by_num[i+1]
		i += 1
	return output


if __name__ == "__main__":
    users = []
    build_users(data_sheet)
    new_users = []
    app.run(debug=True)




#BreweryDB.com API key:  6fd64f4759032d3a5399cc1d49cd4537
#url = "http://api.brewerydb.com/v2/?key=6fd64f4759032d3a5399cc1d49cd4537"
