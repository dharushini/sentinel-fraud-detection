from sentinel.datasets.csv_adapter import load_csv_events

_SPARKOV = """trans_date_trans_time,cc_num,merchant,category,amt,city,lat,long,is_fraud
2025-06-01 12:00:00,1234567890,fraud_Store A,grocery_pos,42.10,NYC,40.71,-74.01,0
2025-06-01 12:05:00,1234567890,fraud_Store B,shopping_net,9.99,NYC,40.71,-74.01,1
"""

_ULB = "Time," + ",".join(f"V{i}" for i in range(1, 29)) + ",Amount,Class\n" + \
       "0," + ",".join("0.1" for _ in range(28)) + ",10.0,0\n" + \
       "5," + ",".join("0.2" for _ in range(28)) + ",20.0,1\n"

_UPI = """timestamp,amount,currency,upi_app,bank,device_fingerprint,status,is_suspicious
2025-12-23T09:57:17.900016,1815.98,INR,Amazon Pay,Axis,device_abc,success,False
2025-12-23T09:57:41.163066,48.52,INR,GPay,SBI,device_xyz,success,True
2025-12-23T09:58:00.000000,999.00,INR,PhonePe,HDFC,device_abc,failed,False
"""

_PAYSIM = """step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud,isFlaggedFraud
1,PAYMENT,9839.64,C1231006815,170136.0,160296.36,M1979787155,0.0,0.0,0,0
1,TRANSFER,181321.0,C1305486145,181321.0,0.0,C553264065,0.0,0.0,1,0
2,CASH_OUT,229133.94,C840083671,15325.0,0.0,C38997010,0.0,214940.76,0,0
"""


def test_sparkov_mapping(tmp_path):
    p = tmp_path / "sp.csv"
    p.write_text(_SPARKOV)
    ev = load_csv_events(str(p), "sparkov")
    assert [e["label"] for e in ev] == [0, 1]
    assert ev[0]["mcc"] == "grocery" and ev[0]["cust_id"] == "cc_1234567890"
    assert ev[1]["channel"] == "online"
    assert ev[0]["ts"] < ev[1]["ts"]


def test_ulb_raw_features(tmp_path):
    p = tmp_path / "ulb.csv"
    p.write_text(_ULB)
    ev = load_csv_events(str(p), "ulb")
    assert ev[0]["raw_features"]["V1"] == 0.1
    assert ev[1]["raw_features"]["Amount"] == 20.0
    assert [e["label"] for e in ev] == [0, 1]


def test_upi_mapping(tmp_path):
    p = tmp_path / "upi.csv"
    p.write_text(_UPI)
    ev = load_csv_events(str(p), "upi")
    # the failed transaction is dropped — a declined attempt never redefines
    # "normal" behaviour, same principle ProfileState.update() applies
    assert len(ev) == 2
    assert [e["label"] for e in ev] == [0, 1]
    assert ev[0]["cust_id"] == "upi_device_abc"
    assert ev[0]["country"] == "IN" and ev[0]["channel"] == "transfer"
    assert ev[0]["merchant_id"] == "Axis_Amazon Pay"
    assert ev[0]["amount"] == 1815.98
    assert ev[0]["ts"] < ev[1]["ts"]


def test_paysim_mapping(tmp_path):
    p = tmp_path / "paysim.csv"
    p.write_text(_PAYSIM)
    ev = load_csv_events(str(p), "paysim")
    assert [e["label"] for e in ev] == [0, 1, 0]
    assert ev[0]["cust_id"] == "ps_C1231006815"
    assert ev[0]["country"] == "IN"
    assert ev[0]["channel"] == "online" and ev[0]["mcc"] == "retail"     # PAYMENT
    assert ev[1]["channel"] == "transfer" and ev[1]["beneficiary"] == "C553264065"  # TRANSFER
    assert ev[2]["channel"] == "atm"                                     # CASH_OUT
    # amounts are log-compressed into Sentinel's realistic INR band, not the
    # raw PaySim simulation units — but relative ordering must be preserved
    assert 0 < ev[0]["amount"] < ev[2]["amount"]
    assert all(e["amount"] < 30000 for e in ev)
    assert ev[0]["ts"] < ev[2]["ts"]


def test_unknown_schema(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n")
    try:
        load_csv_events(str(p), "nope")
        assert False
    except ValueError:
        pass


_IB_TX = """Transaction_ID,Customer_ID,Card_ID,Merchant_ID,Transaction_Date,Transaction_Time,Transaction_Amount,Payment_Method,Transaction_Channel,Device_Type,Transaction_Status,Is_International,Fraud_Flag,Fraud_Reason,Merchant_Risk_Level,Merchant_Category,Customer_State,Customer_City,Merchant_State,Merchant_City
TXN1,CUST1,CARD1,MER1,2024-03-03,05:50:10,21748.02,Debit Card,Mobile App,Android Mobile,Successful,1,1,Unusual Cross-Border / International Transaction,Low,Hotel,Tamil Nadu,Madurai,Tamil Nadu,Salem
TXN2,CUST2,CARD2,MER2,2023-11-08,06:55:15,9960.6,UPI,Online Web,Windows PC,Successful,0,0,None,Low,Grocery,Kerala,Kollam,Haryana,Nuh
TXN3,CUST1,CARD1,MER3,2024-03-04,10:00:00,500.0,Credit Card,POS,POS Terminal,Declined,0,1,Transaction Attempt on Blocked Card,High,Fashion,Tamil Nadu,Madurai,Tamil Nadu,Madurai
"""
_IB_CUST = """Customer_ID,Customer_Name,Gender,Age,Marital_Status,Occupation,Annual_Income,Customer_Segment,State,City,Account_Type,Customer_Since
CUST1,A,Female,67,Married,Retired,500000,Gold,Tamil Nadu,Madurai,Savings,2017-11-13
CUST2,B,Male,24,Single,Engineer,900000,Standard,Kerala,Kollam,Salary,2020-05-05
"""


def test_india_bank_mapping_joins_real_ages_and_skips_declines(tmp_path):
    (tmp_path / "Transaction_Data_250k.csv").write_text(_IB_TX)
    (tmp_path / "Cusmtomer_data.csv").write_text(_IB_CUST)
    ev = load_csv_events(str(tmp_path / "Transaction_Data_250k.csv"), "india_bank")
    assert len(ev) == 2                                   # the declined row is not loaded
    assert [e["cust_id"] for e in ev] == ["ib_CUST2", "ib_CUST1"]   # time-sorted
    upi, hotel = ev
    assert upi["cust_age"] == 24 and hotel["cust_age"] == 67      # ages from the customer table
    assert upi["channel"] == "transfer" and upi["mcc"] == "grocery" and upi["country"] == "IN"
    assert hotel["country"] == "XX" and hotel["mcc"] == "travel" and hotel["label"] == 1
    assert hotel["amount"] == 21748.02 and hotel["card_bin"] == "CARD1"


def test_india_bank_without_customer_table_still_loads(tmp_path):
    (tmp_path / "tx.csv").write_text(_IB_TX)
    ev = load_csv_events(str(tmp_path / "tx.csv"), "india_bank")
    assert len(ev) == 2 and all("cust_age" not in e for e in ev)
