from sqlalchemy import select, insert, delete, update, and_
from sqlalchemy.engine import CursorResult
import sqlalchemy as db
from datetime import datetime, timedelta, timezone
import os
import uuid
import threading
import time

from settings import settings
from models.Schemas import Schemas
from algorithms import SASREM
from algorithms.SASAlgorithms import SASAlgorithms
from Utilities import Utilities
from algorithms import CBSD
from algorithms import Server_WinnForum as WinnForum


class DatabaseController:
    ENGINE = None
    CONNECTION = None
    METADATA = None

    algorithms = SASAlgorithms()
    rem = SASREM.SASREM()

    SETTINGS = None
    USERS = None
    NODES = None
    GRANTS = None
    PUDETECTIONS = None

    __grantRecords = []
    __allRadios = []

    def __init__(self):
        self._delete_db_file()

        if settings.ENVIRONMENT == 'DEVELOPMENT':
            self._connect_to_dev_db()
        else:
            self._connect_to_prod_db()

        # Initialize tables
        self.schemas = Schemas(self.METADATA)
        self._set_tables()
        self._get_tables()

        # Create Admin User
        admin_user = {
            'secondaryUserName': settings.ADMIN_EMAIL,
            'secondaryUserEmail': settings.ADMIN_EMAIL,
            'secondaryUserPassword': settings.ADMIN_PWD,
            'deviceID': '',
            'location': ''
        }
        self.create_user(admin_user, True)

# In[ --- Private Helper Functions --- ]
    def _connect_to_dev_db(self):
        self.ENGINE = db.create_engine(settings.DEVELOPMENT_DATABASE_URI)
        self.CONNECTION = self.ENGINE.connect()
        self.METADATA = db.MetaData()

    def _connect_to_prod_db(self):
        self.ENGINE = db.create_engine(settings.PRODUCTION_DATABASE_URI)
        self.CONNECTION = self.ENGINE.connect()
        self.METADATA = db.MetaData()

    def _execute_query(self, query):
        resultProxy: CursorResult = self.CONNECTION.execute(query)
        queryResult = resultProxy.fetchall()
        rows = [row._asdict() for row in queryResult]

        return rows

    def _delete_table(self, table: db.Table):
        table.drop(self.ENGINE)

    def _delete_all_tables(self):
        self.METADATA.drop_all(self.ENGINE)

    @staticmethod
    def _delete_db_file():
        try:
            os.remove(settings.SQLITE_FILE)
        except Exception as exception:
            print(str(exception))

    def _disconnect_from_db(self):
        self.CONNECTION.close()
        self._delete_db_file()

# In[ --- CREATE TABLES --- ]

    def _set_tables(self):
        self.schemas.create_tables()
        self.METADATA.create_all(self.ENGINE)

    def _get_tables(self):
        self._get_settings_table()
        self._get_secondaryUser_table()
        self._get_nodes_table()
        self._get_grants_table()
        self._get_pudetections_table()

# In[ --- SETTINGS CONTROLS --- ]

    def _get_settings_table(self):
        self.SETTINGS = db.Table(
            settings.SETTINGS_TABLE, self.METADATA, autoload=True, autoload_with=self.ENGINE
        )
        self.create_sas_settings()

    def get_sas_settings(self, algorithm=None):
        if not algorithm:
            query = select([self.SETTINGS])
        else:
            query = select([self.SETTINGS]).where(
                self.SETTINGS.columns.algorithm == algorithm
            )

        try:
            result = self._execute_query(query)[0]
            message = f"GRANT: {result['algorithm']}, " \
                      f"HB: {str(result['heartbeatInterval'])}, " \
                      f"REM: {result['REMAlgorithm']}"
            print(message)

        except Exception as err:
            raise Exception(str(err))

    def set_algorithm_settings(self, result):
        self.algorithms.setGrantAlgorithm(result["algorithm"])
        self.algorithms.setHeartbeatInterval(result["heartbeatInterval"])
        self.algorithms.setREMAlgorithm(result["REMAlgorithm"])

    def create_sas_settings(self, data=None):
        if not data:
            data = {
                'algorithm': 'DEFAULT',
                'heartbeatInterval': 5,
                'REMAlgorithm': 'DEFAULT'
            }

        try:
            self.CONNECTION.execute(self.SETTINGS.insert(), [data])
            self.set_algorithm_settings(data)
        except Exception as err:
            raise Exception(str(err))

    def update_sas_settings(self, data):
        updateQuery = update(self.SETTINGS)\
            .where(self.SETTINGS.columns.algorithm == data.algorithm)\
            .values(
                heartbeatInterval=data['heartbeatInterval'],
                REMAlgorithm=data['REMAlgorithm']
            )
        ResultProxy = self.CONNECTION.execute(updateQuery)

        self.set_algorithm_settings(data)
        self.get_sas_settings()

# In[ --- USER CONTROLS --- ]

    def _get_secondaryUser_table(self):
        self.USERS = db.Table(
            settings.SECONDARY_USER_TABLE, self.METADATA, autoload=True, autoload_with=self.ENGINE
        )

    def get_secondary_users(self):
        query = select([self.USERS])
        try:
            rows = self._execute_query(query)

            return {
                'status': 1,
                'secondaryUsers': rows
            }
        except Exception as err:
            raise Exception(str(err))

    def get_secondary_user(self, payload):
        email = payload['username']
        password = payload['password']

        query = select([self.USERS]).where(and_(
            self.USERS.columns.email == email,
            self.USERS.columns.password == password
        ))

        try:
            rows = self._execute_query(query)

            return {
                'status': 0,
                'user': rows
            }
        except Exception as err:
            raise Exception(str(err))

    def authenticate_user(self, payload: any, isAdmin: bool):
        email = payload['username']
        password = payload['password']

        if not email or not password:
            raise Exception("Username or password not provided")

        query = select([self.USERS]).where(and_(
            self.USERS.columns.email == email,
            self.USERS.columns.password == password,
            self.USERS.columns.admin == isAdmin
        ))
        rows = self._execute_query(query)

        if len(rows) < 1:
            return {
                "status": 0
            }

        return {
            "status": 1,
            "name": rows[0]['username'],
            "id": rows[0]['username'],
            "userType": 'SU' if not rows[0]['admin'] else 'ADMIN'
        }

    def create_user(self, payload, isAdmin):
        username = payload['secondaryUserName']
        email = payload['secondaryUserEmail']
        password = payload['secondaryUserPassword']
        deviceID = payload['deviceID']
        location = payload['location']

        # Sanity Checks
        if not username:
            raise Exception("Username not provided")

        if not email:
            raise Exception("Email Address not provided")

        if not password:
            raise Exception("Password not provided")

        query = select([self.USERS]).where(self.USERS.columns.email == email)
        rows = self._execute_query(query)
        if len(rows) > 0:
            message = f"Email '{email}' already exists. Contact an administrator to recover or reset password."
            return {
                "status": 0,
                "exists": 1,
                "message": message
            }

        insertQuery = insert(self.USERS).values(
            username=username, email=email, admin=isAdmin, password=password, deviceID=deviceID, location=location
        )
        ResultProxy = self.CONNECTION.execute(insertQuery)

        rows = self._execute_query(query)
        if len(rows) < 1:
            return {
                "status": 0,
                "message": "User could not be added. Contact an administrator."
            }

        return {
            "status": 1,
            "message": "User has been added."
        }

    def check_email_availability(self, payload):
        email = payload.get('email', None)

        if not email:
            raise Exception("Email not provided")

        query = select([self.USERS]).where(
            self.USERS.columns.email == email
        )

        try:
            rows = self._execute_query(query)

            if len(rows) > 1:
                return {
                    'status': 1,
                    'exists': 1,
                    'message': 'Email In Use'
                }
            return {
                'status': 1,
                'exists': 0,
                'message': 'Email Unique'
            }
        except Exception as err:
            raise Exception(str(err))

# In[ --- NODE CONTROLS --- ]

    def _get_nodes_table(self):
        self.NODES = db.Table(
            settings.NODE_TABLE, self.METADATA, autoload=True, autoload_with=self.ENGINE
        )

    def get_nodes(self):
        query = select([self.NODES])
        try:
            rows = self._execute_query(query)

            return {
                'status': 1,
                'nodes': rows
            }
        except Exception as err:
            raise Exception(str(err))

    def _get_node_by_cbsdId(self, cbsdId):
        query = select([self.NODES]).where(
            self.NODES.columns.cbsdId == cbsdId
        )

        try:
            rows = self._execute_query(query)

            if len(rows) > 0:
                return Utilities.loadCBSDFromJSON(rows[0])

            return None

        except Exception as err:
            raise Exception(str(err))

    def register_nodes(self, sid, payload):
        responseArr, assignmentArr, insertionArr = [], [], []

        for item in payload["registrationRequest"]:
            radioID = item.get('fccId', str(uuid.uuid4()))
            item['fccId'] = radioID
            # Sanity Checks
            if not radioID:
                raise Exception("Radio ID not provided")

            query = select([self.NODES]).where(and_(
                self.NODES.columns.fccId == radioID,
                self.NODES.columns.IPAddress == item['IPAddress']
            ))
            rows = self._execute_query(query)
            if len(rows) > 0:
                message = f"Node '{radioID}' already exists with the same IP Address. Node creation rollback complete"
                return {
                    "status": 0,
                    "exists": 1,
                    "message": message
                }

            insertionArr.append(item)

            radio = CBSD.CBSD(radioID, 5, radioID)
            # Flatten Structure
            location = item['location'].split(',')
            radio.latitude = location[0]
            radio.longitude = location[1]
            radio.nodeType = item["nodeType"]
            radio.minFrequency = item["minFrequency"]
            radio.maxFrequency = item["maxFrequency"]
            radio.minSampleRate = item["minSampleRate"]
            radio.maxSampleRate = item["maxSampleRate"]
            radio.mobility = item["mobility"]
            radio.id = radioID

            response = WinnForum.RegistrationResponse(radio.id, None, self.algorithms.generateResponse(0))

            if "measCapability" in item:
                cbsd = SASREM.CBSDSocket(radio.id, sid, False)
                assignmentArr.append(cbsd)
                response.measReportConfig = item["measCapability"]

            responseArr.append(response.asdict())

        try:
            self.CONNECTION.execute(self.NODES.insert(), insertionArr)
        except Exception as err:
            print(str(err))
            return {
                "status": 0,
                "message": f"Nodes could not be added due to '{str(err)}'. Contact an administrator."
            }

        return {
            "status": 1,
            "message": "Nodes have been added.",
            "registrationResponse": responseArr
        }, assignmentArr

    def deregister_nodes(self, nodes):
        try:
            responseArr = []
            for item in nodes["deregistrationRequest"]:
                if 'fccId' not in item or not item['fccId']:
                    raise Exception(str('FCC-ID not provided'))
                if 'cbsdId' not in item or not item['cbsdId']:
                    raise Exception(str('CBSD-ID not provided'))

                query = delete([self.NODES]).where(and_(
                    self.NODES.columns.fccId == item['fccId'],
                    self.NODES.columns.cbsdId == item['cbsdId']
                ))
                rows = self._execute_query(query)

                response = WinnForum.DeregistrationResponse()
                response.cbsdId = item['cbsdId']

                query = select([self.NODES]).where(and_(
                    self.NODES.columns.fccId == item['fccId'],
                    self.NODES.columns.cbsdId == item['cbsdId']
                ))
                rows = self._execute_query(query)

                if len(rows) > 0:
                    response.response = self.algorithms.generateResponse(0)
                else:
                    response.response = self.algorithms.generateResponse(103)

                responseArr.append(response.asdict())

            return {
                'status': 0,
                'message': 'Node Deregistration complete',
                'deregistrationResponse': responseArr
            }

        except Exception as err:
            return {
                'status': 1,
                'message': str(err)
            }

    def update_nodes(self, payload):
        try:
            updateQuery = update(self.NODES) \
                .where(self.NODES.columns.nodeID == payload['nodeID']) \
                .values(
                    trustLevel=payload['trustLevel'], IPAddress=payload['IPAddress'],
                    minFrequency=payload['minFrequency'], maxFrequency=payload['maxFrequency'],
                    minSampleRate=payload['minSampleRate'], maxSampleRate=payload['maxSampleRate'],
                    nodeType=payload['nodeType'], mobility=payload['mobility'], status=payload['status'],
                    comment=payload['comment'], fccId=payload['fccId'], cbsdSerialNumber=payload['cbsdSerialNumber'],
                    callSign=payload['callSign'], cbsdCategory=payload['cbsdCategory'], cbsdInfo=payload['cbsdInfo'],
                    airInterface=payload['airInterface'], installationParam=payload['installationParam'],
                    measCapability=payload['measCapability'], groupingParam=payload['groupingParam']
                )
            ResultProxy = self.CONNECTION.execute(updateQuery)

        except Exception as err:
            raise Exception(str(err))

# In[ --- GRANT CONTROLS --- ]

    def get_grant_records(self):
        return self.__grantRecords

    def get_grants(self):
        query = select([self.GRANTS])
        try:
            rows = self._execute_query(query)

            return {
                'status': 1,
                'spectrumGrants': rows
            }
        except Exception as err:
            raise Exception(str(err))

    def get_grant_with_id(self, grantId):
        query = select([self.GRANTS]).where(
            self.GRANTS.columns.grantId == grantId
        )
        rows = self._execute_query(query)

        if len(rows) == 0:
            for grant in self.__grantRecords:
                if str(grant.id) == str(grantId):
                    return grant
            raise Exception(f"Grant with Id {grantId} not found")
        else:
            grant = Utilities.loadGrantFromJSON(rows[0])

            return grant

    def _get_grants_table(self):
        self.GRANTS = db.Table(
            settings.GRANT_TABLE, self.METADATA, autoload=True, autoload_with=self.ENGINE
        )

    def create_grant_request(self, payload):
        responseArr, insertionArr = [], []

        for item in payload["grantRequest"]:
            query = select([self.GRANTS]).where(and_(
                self.GRANTS.columns.secondaryUserID == item['secondaryUserID'],
                self.GRANTS.columns.minFrequency == item['minFrequency'],
                self.GRANTS.columns.startTime == item['startTime']
            ))
            rows = self._execute_query(query)

            if len(rows) > 0:
                message = f"Grant already exists. Grant processing rollback complete"
                return {
                    "status": 0,
                    "exists": 1,
                    "message": message
                }

            grantRequest = WinnForum.GrantRequest(item["secondaryUserID"], None)
            ofr = WinnForum.FrequencyRange(item["minFrequency"], item["maxFrequency"])
            grantRequest.operationParam = WinnForum.OperationParam(item["powerLevel"], ofr)
            vtgp = WinnForum.VTGrantParams(
                None, None, item["preferredFrequency"], item["frequencyAbsolute"],
                item["minBandwidth"], item["preferredBandwidth"], item["preferredBandwidth"],
                item["startTime"], item["endTime"], item["approximateByteSize"],
                item["dataType"], item["powerLevel"], item["location"], item["mobility"],
                item["maxVelocity"]
            )
            grantRequest.vtGrantParams = vtgp

            grantResponse = self.algorithms.runGrantAlgorithm(self.__grantRecords, self.rem, grantRequest)  # algorithm
            grantResponse.grantId = str(uuid.uuid4())

            if grantResponse.response.responseCode == "0":
                g = WinnForum.Grant(
                    grantResponse.grantId, item["cbsdId"], grantResponse.operationParam,
                    vtgp, grantResponse.grantExpireTime
                )
                self.__grantRecords.append(g)

            responseArr.append(grantResponse.asdict())
            insertionArr.append(item)

        try:
            self.CONNECTION.execute(self.GRANTS.insert(), insertionArr)
        except Exception as err:
            print(str(err))
            return {
                "status": 0,
                "message": f"Grant request could not be completed due to '{str(err)}'. Contact an administrator."
            }

        return {
            "status": 1,
            "message": "Grants have been created",
            "grantResponse": responseArr
        }

    def heartbeat_request(self, data):
        try:
            heartbeatArr, grantArr = [], []

            for heartbeat in data["heartbeatRequest"]:
                cbsdQuery = select([self.NODES]).where(self.NODES.columns.cbsdId == heartbeat['cbsdId'])
                cbsd = self._execute_query(cbsdQuery)[0]

                grantQuery = select([self.GRANTS]).where(self.GRANTS.columns.grantId == heartbeat['grantId'])
                grant = self._execute_query(grantQuery)[0]

                if heartbeat["measReport"]:
                    for rpmr in heartbeat["measReport"]["rcvdPowerMeasReports"]:
                        # TODO: check to see if frequency range already exists as a submission
                        #  from specific CBSD to prevent spamming
                        mr = Utilities.measReportObjectFromJSON(rpmr)
                        self.rem.measReportToSASREMObject(mr, cbsd)

                response = self.algorithms.runHeartbeatAlgorithm(self.__grantRecords, self.rem, heartbeat, grant)
                grant.heartbeatTime = datetime.now(timezone.utc)
                grant.heartbeatInterval = response.heartbeatInterval
                grantArr.append(grant)
                heartbeatArr.append(response.asdict())

            return {
                'status': 0,
                "heartbeatResponse": heartbeatArr
            }, grantArr

        except Exception as err:
            return {
                'status': 1,
                "message": str(err)
            }, []

    def cancel_grant(self, grant):
        now = datetime.now(timezone.utc)
        if grant.heartbeatTime + timedelta(0, grant.heartbeatInterval) < now:
            query = delete([self.GRANTS]).where(and_(
                self.GRANTS.columns.grantId == grant['grantId'],
                self.GRANTS.columns.secondaryUserID == grant['secondaryUserID']
            ))
            row = self._execute_query(query)[0]

            print(f"Grant {grant['grantId']} has been cancelled")

    def spectrum_inquiry(self, data):
        try:
            inquiryArr = []
            radiosToChangeBack = []
            radiosToCommunicate = []

            for request in data["spectrumInquiryRequest"]:
                response = WinnForum.SpectrumInquiryResponse(
                    request["cbsdId"], [], self.algorithms.generateResponse(0)
                )

                for fr in request["inquiredSpectrum"]:
                    lowFreq, highFreq = int(fr["lowFrequency"]), int(fr["highFrequency"])
                    channelType, ruleApplied = "PAL", "FCC_PART_96"
                    maxEirp = self.algorithms.getMaxEIRP()

                    if self.algorithms.acceptableRange(lowFreq, highFreq):
                        if 3700000000 > highFreq > 3650000000:
                            channelType = "GAA"

                        present = self.algorithms.isPUPresentREM(
                            self.rem, highFreq, lowFreq, None, None, None
                        )
                        if present == 0:
                            fr = WinnForum.FrequencyRange(lowFreq, highFreq)
                            availChan = WinnForum.AvailableChannel(fr, channelType, ruleApplied, maxEirp)
                            response.availableChannel.append(availChan)
                        elif present == 2:
                            # TODO: Remove allRadios
                            rTCB, rTC = Utilities.initiateSensing(
                                lowFreq, highFreq, self.__allRadios
                            )
                            radiosToChangeBack.extend(rTCB)
                            radiosToCommunicate.extend(rTC)

                inquiryArr.append(response.asdict())

            threading.Timer(
                3.0, Utilities.resetRadioStatuses, [radiosToChangeBack]
            ).start()

            return {
                'status': 0,
                "spectrumInquiryResponse": inquiryArr
            }, radiosToCommunicate

        except Exception as err:
            return {
                'status': 1,
                "message": str(err)
            }, []

    def relinquishment_request(self, data):
        relinquishArr = []

        for relinquishmentRequest in data["relinquishmentRequest"]:
            # TODO: Connect to DB
            # params = {
            #     "cbsdId": relinquishmentRequest["cbsdId"],
            #     "grantId": relinquishmentRequest["grantId"],
            #     "action": "relinquishGrant"
            # }
            # if databaseLogging:
            #     sendPostRequest(params)

            success = Utilities.removeGrant(
                self.get_grant_with_id(relinquishmentRequest["grantId"]).id,
                relinquishmentRequest["cbsdId"],
                self.__grantRecords
            )

            response = {
                "cbsdId": relinquishmentRequest["cbsdId"],
                "grantId": relinquishmentRequest["grantId"]
            }

            if relinquishmentRequest["cbsdId"] is None or relinquishmentRequest["grantId"] is None:
                response["response"] = Utilities.generateResponse(102)
            elif success:
                response["response"] = Utilities.generateResponse(0)
            else:
                response["response"] = Utilities.generateResponse(103)
            relinquishArr.append(response)

        return {"relinquishmentResponse": relinquishArr}

    def spectrumData(self, jsonData):
        try:
            cbsd = self._get_node_by_cbsdId(jsonData["spectrumData"]["cbsdId"])

            if cbsd:
                deviceInfo = jsonData["spectrumData"]
                cbsd.latitude = deviceInfo["latitude"]
                cbsd.longitude = deviceInfo["longitude"]
                # TODO: # If simulating, dump previously logged data
                # if (isSimulating):
                #     REM.objects = []

                if deviceInfo["spectrumData"]:
                    for rpmr in deviceInfo["spectrumData"]["rcvdPowerMeasReports"]:
                        mr = Utilities.measReportObjectFromJSON(rpmr)
                        self.rem.measReportToSASREMObject(mr, cbsd)

        except Exception as err:
            raise Exception(str(err))

# In[ --- PU DETECTIONS CONTROLS --- ]

    def _get_pudetections_table(self):
        self.PUDETECTIONS = db.Table(
            settings.PUDETECTIONS, self.METADATA, autoload=True, autoload_with=self.ENGINE
        )

    def get_pudetections(self):
        query = select([self.PUDETECTIONS])

        try:
            rows = self._execute_query(query)

            return {
                'status': 1,
                'puDetections': rows
            }
        except Exception as err:
            raise Exception(str(err))

    def get_pudetection_by_id(self, reportId):
        query = select([self.PUDETECTIONS]).where(
            self.PUDETECTIONS.columns.reportId == reportId
        )

        try:
            rows = self._execute_query(query)

            return {
                'status': 1,
                'puDetection': rows
            }
        except Exception as err:
            raise Exception(str(err))

    def set_pudetections(self, insertionArr):
        try:
            self.CONNECTION.execute(self.PUDETECTIONS.insert(), insertionArr)
        except Exception as err:
            print(str(err))
            return {
                "status": 0,
                "message": f"PU Detections could not be added due to '{str(err)}'."
            }

        return {
            "status": 1,
            "message": "PU Detections recorded"
        }

    def check_pudetections(self, data=None):
        report, pauseArr, insertionArr = [], [], []
        freqRange = self.algorithms.MAXCBRSFREQ - self.algorithms.MINCBRSFREQ
        blocks = freqRange/self.algorithms.TENMHZ
        grants = self.get_grants()['spectrumGrants']

        for i in range(int(blocks)):
            low = (i * self.algorithms.TENMHZ) + self.algorithms.MINCBRSFREQ
            high = ((i + 1) * self.algorithms.TENMHZ) + self.algorithms.MINCBRSFREQ
            result = self.algorithms.isPUPresentREM(
                self.rem, low, high, None, None, None
            )

            if result == 1:
                if data:
                    insertionArr.append({
                        "reportId": data["reportId"],
                        "timestamp": str(int(time.time())),
                        "lowFreq": low,
                        "highFreq": high,
                        "result": int(result)
                    })

                    for grant in grants:
                        isOverlap = self.algorithms.frequencyOverlap(
                            low, high, grant.minFrequency, grant.maxFrequency
                        )
                        # TODO: CBSD and NODE are not in sync. Currently, cbsd doesn't have an sid
                        node = self._get_node_by_cbsdId(grant.cbsdId)

                        if isOverlap and node:
                            pauseArr.append({
                                'grantId': grant.grantId,
                                'sid': node.sid
                            })
                    report.append("PU FOUND")
            elif result == 0:
                report.append("PU NOT FOUND")
            elif result == 2:
                report.append("NO SPECTRUM DATA")

        self.set_pudetections(insertionArr)

        return report, pauseArr

# In[ --- OTHERS --- ]

    def incumbentInformation(self, incumbentData):
        """Function for PUs to send their operating data"""
        utilizeExtraChannel, obfuscated = True, []  # TODO: Decide when to toggle this

        for data in incumbentData["incumbentInformation"]:
            lowFreq, highFreq = None, None
            # Get time, location, and frequency range of PU
            desireObfuscation, scheme, startTime, endTime = [None] * 4
            puLat, puLon, puLowFreq, puLighFreq, power = [None] * 5

            try:
                desireObfuscation = bool(data["desireObfuscation"])
                scheme = str(data["scheme"])
                puLowFreq = float(data["lowFreq"])
                puHighFreq = float(data["highFreq"])
                puLat = data["puLat"]
                puLon = data["puLon"]
                power = data["power"]
                startTime = data["startTime"]
                endTime = data["endTime"]
            except Exception as err:
                raise Exception(str(err))

            if desireObfuscation:
                if scheme:
                    # TODO: Remove allRadios
                    est_num_of_available_sus = 0
                    for radio in self.__allRadios:
                        if not radio.justChangedParams:
                            est_num_of_available_sus += 1

                    if scheme == "double_pad":
                        lowFreq, highFreq = Utilities.double_pad_obfuscate(
                            puLowFreq, puHighFreq, est_num_of_available_sus
                        )
                    elif scheme == "fill_channel":
                        lowFreq, highFreq = Utilities.fill_channel_obfuscate(
                            puLowFreq, puHighFreq, est_num_of_available_sus
                        )

                    obfuscated.extend(self.sendIICCommand(lowFreq, highFreq))
                else:
                    print("No PU Obfuscation Scheme Detected...")
            else:
                pass  # PU does not want special treatment

        return obfuscated

    def sendIICCommand(self, lowFreq, highFreq):
        """Will ask 1 idle node to transmit over the low-high freq"""

        radiosToChangeBack, obstructionArr = [], []
        # TODO: Remove radios
        for radio in self.__allRadios:
            if not radio.justChangedParams:
                # print("SENDING LOW: " +str(lowFreq)+" and HIGH: "+str(highFreq))
                radio.justChangedParams = True
                obstructionArr.append((radio, lowFreq, highFreq))
                radiosToChangeBack.append(radio)

        threading.Timer(
            5.0, Utilities.resetRadioStatuses, [radiosToChangeBack]
        ).start()

        return obstructionArr
