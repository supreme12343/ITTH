INDIAN SCHOOL AL WADI AL KABIR

Worksheet (2026-27)

Class: XII Comp. Sci. | Department: Computer Science | Execute these questions in Lab

Worksheet No: 4 | Topic: Data Files – BINARY  FILES | Note: Revise them at home too

1 | A binary file “Book.dat” has structure [BookNo, Book_Name, Author, Price].
Write a user defined function CreateFile() to input data for a record and add to Book.dat .
Write a function CountRec(Author) in Python which accepts the Author name as parameter and count and return number of books by the given Author are stored in the binary file “Book.dat

2 | A binary file “STUDENT.DAT” has structure [admission_number, Name, Percentage]. Write a function countrec() in Python that would read contents of the file “STUDENT.DAT” and display the details of those students whose percentage is above 75. Also display number of students scoring above 75%.

3 | Write a function in python to search and display details, whose destination is “Cochin” from binary file  
 “Bus.Dat”. Assuming the binary file is containing the following elements in the list:
Bus Number
Bus Starting Point
Bus Destination

4 | Write a function addrec() in Python to add more new records at the bottom of a binary file  
 “STUDENT.dat”, assuming the binary file is containing the following structure :
        [Roll Number, Student Name]

5 | Write a function searchprod( pc) in python to display the record of a particular product from a file product.dat whose code is passed as an argument. Structure of product contains the following elements [product code , product price]

6 | Write a function routechange(route number) which takes the Route number as parameter and modify the route name(Accept it from the user) of passed route number in a binary file “route.dat” that contains [rno,rname,distance]

7 | Write a function countrec(sport name) in Python which accepts the name of sport as parameter and count and display the coach name of a sport which is passed as argument from the binary file “sport.dat”. Structure of record in a file is  [sport name, coach name]

8 | A binary file “salary.dat” has structure [employee id, employee name, salary]. Write a function update(name) in Python that would read contents of the file “salary.dat” and change the salary of the employee to 20000 whose name is accepted by the function.

9 | Amit is a monitor of class XII-A and he stored the record of all the students of his class in a file named “class.dat”. Structure of record is [roll number, name, percentage]. His computer teacher has assigned the following duty to Amit
Write a function copyrec() to copy the details of all students whose percentage is above 80 to another file topper.dat

10 | A binary file “emp.dat” has structure [employee id, employee name]. Write a function delrec(employee number) in Python that would read contents of the file “emp.dat” and delete the details of those employee whose employee number is passed as argument.

11 | A Binary file, CINEMA.DAT has the following structure:
 {MNO:[MNAME, MTYPE]}
 Write a user defined function, findType(mtype), that accepts mtype as parameter and displays all the   records from the binary file CINEMA.DAT, that have the value of Movie Type as mtype.

12 | A binary file candidate.dat contains record of the following format: [candidateid,name, designation , 
   experience] 
   (I) Write a function to input the data of a candidate and append it in a binary file.
  (II) Write a function to update the data of candidates whose experience is more than 10 years and    
         change their designation to "Senior Manager".
 (III) Write a function to read the data from the binary file and display the data of all those candidates   
         who are not "Senior Manager".

13 | Explain the functions of tell() and seek() with examples.

14 | What is the difference between text files and binary files ?

15 | What is the difference between the following modes of opening a file:
‘w’ and ‘a’ 
‘rb’ and ‘rb+’
‘w’ and ‘wb’