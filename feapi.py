# A pyhton script for subimission of files to FireEye AX (Malware Analysis System) for analysis, load-blancing across multiple unit
# this is a fork of https://github.com/FeyeAPI/FireEye-AX-API
# This code is providded as is with no support from FireEye 
# Created By: Mohamed Al-Shabrawy
# Repository: https://github.com/m-shabrawy/
# Version: 2.0.0
# Updated: 22-12-2021

import configparser
import argparse
import os
import requests
import sys
import json
import errno
from io import StringIO
import datetime
import hashlib
import sqlite3
import logging
import logging.handlers
from lxml import etree

requests.packages.urllib3.disable_warnings()
config = configparser.ConfigParser()
config.read(".feapi.ini")

un = config.get('AX Config', 'un')
pw = config.get('AX Config', 'pw')
AXs = config.items('MAS')
Tokens = []

baseDir = config.get('Local Config', 'baseDir')
feDirs = config.get('Local Config', 'feDirs')
resultDirs = config.get('Local Config', 'resultDirs')
dbDir = config.get('Local Config', 'dbDir')
db = config.get('Local Config', 'db')
logDir = config.get('Local Config', 'logDir')
logFile = config.get('Local Config', 'logFile')

application = config.get('Payload Config', 'application')
timeout = config.get('Payload Config', 'timeout')
priority = config.get('Payload Config', 'priority')
analysistype = config.get('Payload Config', 'analysistype')
force = config.get('Payload Config', 'force')
prefetch = config.get('Payload Config', 'prefetch')



NS = '{http://www.fireeye.com/alert/2013/AlertSchema}'


def instantiate_logs():
    fq_log_name = os.path.join(logDir, logFile)

    global mylogger
    mylogger = logging.getLogger(__name__)
    mylogger.setLevel(logging.DEBUG)
    myformatter = logging.Formatter('%(asctime)s - %(funcName)s - %(levelname)s - %(message)s')
    myhandler = logging.handlers.RotatingFileHandler(fq_log_name, maxBytes=10485760, backupCount=5, )
    myhandler.setLevel(logging.DEBUG)
    myhandler.setFormatter(myformatter)
    mylogger.addHandler(myhandler)
    return mylogger


def setup():
    try:
        os.makedirs(logDir)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    mylogger = instantiate_logs()
    mylogger.info("Instantiated %s in %s" % (logFile, logDir))
    for adirectory in feDirs.split(',', ):
        middir = os.path.join(baseDir, adirectory)
        for subdir in resultDirs.split(',', ):
            final = os.path.join(middir, subdir)
            try:
                os.makedirs(final)
                mylogger.info(u"Setup created directory {0:s}".format(final))
            except OSError as e:
                if e.errno != errno.EEXIST:
                    mylogger.error(e)
                    raise
    try:
        os.makedirs(dbDir)
        mylogger.info(u"Setup created directory {0:s}".format(dbDir))
    except OSError as e:
        if e.errno != errno.EEXIST:
            mylogger.error(e)
            raise
    # noinspection PyBroadException
    try:
        assert isinstance(dbDir, str)
        database = os.path.join(dbDir, db)
        if not os.path.exists(database):
            conn = sqlite3.connect(database)
            conn.execute('''CREATE TABLE files
            (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            hash TEXT NOT NULL,
            filename TEXT NOT NULL,
            start INT NOT NULL,
            complete INT,
            engine TEXT,
            analysis_id TEXT,
            result TEXT,
            malware_name TEXT,
            analysis_url TEXT,
            ax TEXT);''')
            conn.close()
            mylogger.info("%s database with 'files' table created" % database)
        else:
            mylogger.info("Skipped creating %s because it already exists." % database)
    except:
        mylogger.error("Failed creating %s" % database)
        mylogger.error(sys.exc_info()[0])

    mylogger.handlers[0].close()
    mylogger.removeHandler(mylogger)


def login(un, pw, ax):
    baseUrl = 'https://%s:443/wsapis/v2.0.0/' % ax
    reqUrl = baseUrl + 'auth/login'
    c = requests.post(reqUrl, auth=(un, pw), verify=False)
    if int(c.status_code) == 200:
        mylogger.info("%s successfully logged in to %s" % (un, ax))
        apiToken = c.headers['X-FeApi-Token']
        return apiToken
    elif int(c.status_code) == 401:
        mylogger.error("%s failed logging in to %s" % (un, ax))
    elif int(c.status_code) == 503:
        mylogger.error("%s Web Services API not enabled.  Please enable and try again." % ax)
    else:
        mylogger.error("Log in to %s failed for some unspecified reason." % ax)


def logout(token,mas):
    baseUrl = 'https://%s:443/wsapis/v1.1.0/' % mas
    auth_header = {'X-FeApi-Token': token}
    reqUrl = baseUrl + 'auth/logout?'
    c = requests.post(reqUrl, headers=auth_header, verify=False)
    if int(c.status_code) == 204:
        mylogger.info("Successfully logged out of %s." % mas)
        return "Logged out"
    else:
        mylogger.info(u"Logout from {0:s} failed for some unspecified reason".format(mas))


def get_fe_config(mas):
    instantiate_logs()
    token = login(un, pw, mas)
    auth_header = {'X-FeApi-Token': token}
    baseUrl = 'https://%s:443/wsapis/v2.0.0/' % mas
    reqUrl = baseUrl + 'config'
    c = requests.get(reqUrl, headers=auth_header, verify=False)
    mylogger.info(c.text)
    print (c.text)


def calc_hash(fileName):
    BLOCKSIZE = 65536
    hasher = hashlib.md5()
    with open(fileName, 'rb') as afile:
        buf = afile.read(BLOCKSIZE)
        while len(buf) > 0:
            hasher.update(buf)
            buf = afile.read(BLOCKSIZE)
    fHash = hasher.hexdigest()
    afile.close()
    mylogger.info("md5 hash of %s = %s" % (fileName, fHash))
    return fHash


def submit_for_analysis(ax, token, fqfn):
    profileDir, fName = os.path.split(fqfn)
    baseDir, profile = os.path.split(profileDir)

    fHash = calc_hash(fqfn)
    cursor = conn.execute("""select datetime(start, '+1 day'), result from files where hash = ? and engine = ? and ax = ?""",
                          (fHash, profile, ax))
    for row in cursor:
        raTime = datetime.datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
        if raTime >= datetime.datetime.now() or row[1] == 'pending':
            mylogger.warn(
                "Skipping analysis of %s because existing analysis less than 1 day old or is currently in process." % (
                    fqfn,))
            mylogger.warn("%s eligible for reanalysis at %s" % (fqfn, str(raTime)))
            os.remove(fqfn)
            mylogger.warn("Removed/Deleted %s from analysis queue" % (fqfn,))
            return

    auth_header = {'X-FeApi-Token': token}

    payload = {'filename': fName, 'options': '{"application":"%s","timeout":"%s","priority":"%s","profiles":["%s"],'
                                             '"analysistype":"%s", "force":"%s","prefetch":"%s"}' % (
                                                 application, timeout, priority, profile, analysistype, force,
                                                 prefetch)}
    baseUrl = 'https://%s:443/wsapis/v1.1.0/' % ax
    reqUrl = baseUrl + 'submissions'
    mylogger.info(fName)
    mylogger.info(payload)
    with open(fqfn, 'rb') as file_content:
        submitted_file = {'file': file_content}
        c = requests.post(reqUrl, headers=auth_header, verify=False, data=payload, files=submitted_file)

    if int(c.status_code) == 200:
        analysis_id = json.loads(c.text)[0]['ID']
        mylogger.info("File Analysis ID = %s" % analysis_id)
        now = str(datetime.datetime.now())
        dstFileName = os.path.join(profileDir, 'Pending', fName)
        os.rename(fqfn, dstFileName)
        conn.execute("""insert into files
                    (hash, filename, start, engine, analysis_id, result, ax)
                    values (?,?,?,?,?,?,?)""",
                     (fHash, dstFileName, now, profile, analysis_id, 'pending',ax)
                     )
        conn.commit()
        mylogger.info("Submitted %s to profile %s for analysis." % (fqfn, profile))

    elif int(c.status_code) == 400:
        mylogger.warn("Submission of %s failed because the filter value was invalid" % (fqfn,))
    else:
        mylogger.warn("Submission of %s failed with a status code of %s" % (fqfn, str(c.status_code)))
        mylogger.error(c.text)
        pass


def process_results(alert_obj, fqfn):
    elem = alert_obj.find(NS + 'alert-url')
    alert_url = elem.text

    compDate = str(datetime.datetime.now())

    if alert_obj.attrib['severity'] == 'majr':
        mylogger.info("%s received a verdict of malicious." % fqfn)
        fileResult = 'Bad'
    elif alert_obj.attrib['severity'] == 'minr':
        mylogger.info("%s received a verdict of clean." % fqfn)
        fileResult = 'Good'
    else:
        mylogger.warn("%s received an unanticipated verdict of: %s" % (fqfn, str(alert_obj.attrib['severity'])))
        fileResult = 'Unk'

    srcDir, fName = os.path.split(fqfn)
    profileDir, toDiscard = os.path.split(srcDir)
    toDiscard, profile = os.path.split(profileDir)
    destFileName = os.path.join(profileDir, fileResult, fName)
    malwareNames = []
    fHash = calc_hash(fqfn)
    # for each instance of the malware Element found
    for b in alert_obj.iter(NS + 'malware'):
        try:
            if not b.attrib['name'] in malwareNames:
                malwareNames.append(b.attrib['name'])
        except:
            pass

        elem = b.find(NS + 'md5sum')
        fileHash = elem.text

        if fileHash != fHash:
            mylogger.error(
                "Hash returned in analysis results (%s) does not equal hash of file on disk (%s)." % (
                    fileHash, fHash))
            #sys.exit(1)

    os.rename(fqfn, destFileName)
    mylogger.info("moved %s to %s" % (fqfn, destFileName))

    try:
        conn.execute("""UPDATE files
                        SET filename = ?,
                        complete = ?,
                        result = ?,
                        malware_name = ?,
                        analysis_url = ?
                        WHERE hash = ? and engine = ? and ax = """,
                     (destFileName, compDate, fileResult, str(malwareNames), alert_url, fileHash, profile, ax))
        conn.commit()
    except sqlite3.Error as e:
        mylogger.error(e)
        sys.exit(1)


def get_results(ax, token, analysis_id, fqfn):
    auth_header = {'X-FeApi-Token': token}
    baseUrl = 'https://%s:443/wsapis/v2.0.0/' % ax
    reqUrl = baseUrl + 'submissions/results/' + str(analysis_id) + '?info_level=normal'

    c = requests.get(reqUrl, headers=auth_header, verify=False)

    if int(c.status_code) == 200:
        mylogger.info("Analysis of %s completed." % (fqfn,))
        foo = c.content.replace('encoding="UTF-8"', '')
        tree = etree.parse(StringIO(foo))
        NS = '{http://www.fireeye.com/alert/2013/AlertSchema}'
        # 'a' will be an alert Element we can iterate over
        for a in tree.iter(NS + 'alert'):
            process_results(a, fqfn)

    elif int(c.status_code) == 401:
        mylogger.warn("get_results request unsuccessful due to incorrect session token (not logged in)")
    elif int(c.status_code) == 404:
        mylogger.warn(
            "get_results request unsuccessful for %s due to incorrect/unknown analysis_d: %s" % (fqfn, analysis_id))
    elif int(c.status_code) == 500:
        mylogger.info(
            "get_results request for %s still processing as analysis_id: %s.  Try again later." % (fqfn, analysis_id))


def check_submission(ax, token, analysis_id, fqfn):
    auth_header = {'X-FeApi-Token': token}
    baseUrl = 'https://%s:443/wsapis/v1.1.0/' % ax
    reqUrl = baseUrl + 'submissions/status/' + str(analysis_id)

    c = requests.get(reqUrl, headers=auth_header, verify=False)

    if int(c.status_code) == 200:
        subStatus = c.json()['submissionStatus']
        if subStatus == "Done":
            mylogger.info("Analysis of %s with analysis_id %s completed on %s." % (fqfn, analysis_id, ax))
            get_results(token, analysis_id, fqfn)
        elif subStatus == "Submission not found":
            mylogger.warn("check_submission request failed.  Could not find analysis_id %s" % (analysis_id,))
        elif subStatus == "In Progress":
            mylogger.info("check_submission request still pending for %s using analysis_id %s" % (fqfn, analysis_id))
        else:
            mylogger.warn(
                "check_submission request for %s, analysis_id %s returned an unexpected value: %s" % (
                    fqfn, analysis_id, str(c.text)))
            mylogger.warn("%s" % (c.json()))
    elif int(c.status_code) == 401:
        mylogger.warn("check_submission request unsuccessful due to incorrect session token (not logged in)")
    elif int(c.status_code) == 404:
        mylogger.warn("check_submission request unsuccessful for %s due to incorrect/unknown analysis_d: %s" % (
            fqfn, analysis_id))
    else:
        mylogger.warn(
            "check_submission for %s, analysis_id %s returned an unexpected status_code: %s" % (
                fqfn, analysis_id, str(c.status_code)))


def check_pending_analyses():
    for ax, token in Tokens:
        query = "select analysis_id, filename from files where result = 'pending' and ax = '%s'" % ax
        cursor = conn.execute(query)
        for row in cursor:
            # analysis_id = row[0]
            # fully_qualified_file_name =  row[1]
            mylogger.info("Checking submission status for %s, analysis_id %s" % (row[1], row[0]))
            check_submission(ax, token, row[0], row[1])


def submit_new_files(Tokens):
    # for fileName found in base_dir/fe_dirs that aren't dirs
    for adirectory in feDirs.split(',', ):
        searchDir = os.path.join(baseDir, adirectory)
        files_to_process = os.listdir(searchDir)
        i = 0
        for fn in files_to_process:
            fqfn = os.path.join(baseDir, adirectory, fn)
            if os.path.isfile(fqfn):
                mylogger.info("Submitting %s for analysis on %s." % (fqfn,))
                submit_for_analysis(Tokens[i][0],Tokens[i][1], fqfn)
                i = i+1
            if i > len(Tokens): i = 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--setup", help="Option to create directories and databases needed for the tool")
    args = parser.parse_args()
    # Run setup if we are asked to
    if args.setup:
        setup()
    # let's start logging
    mylogger = instantiate_logs()
    # let's hunt some treasures from DB
    database = os.path.join(dbDir, db)
    conn = sqlite3.connect(database)
    # now we build the empire of connections
    for key, ax in AXs:
        token = login(un, pw)
        Tokens.append([ax,token])
    # So we are good to go after eleminating any offline AX
    for ax, token in Tokens:
        check_pending_analyses(ax,token)
    # Submit new files going round-robin across all AXs
    submit_new_files(Tokens)
    # We are done let's get out of here
    for ax, token in Tokens:
        logout(ax,token)
    conn.close()
